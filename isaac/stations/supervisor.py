"""Ordered start, signal-driven stop and reverse-order shutdown for one station.

Components start in the declared order and close in exact reverse order of the
ones that actually started; a failed start closes only its predecessors. The
SIGTERM/SIGINT handler only records the request: the owner thread finishes the
current simulation step, then stops at a safe boundary. A step that never
returns cannot be stopped by this process; the service manager's stop timeout
then kills it and the next start reports the missing ``stopped`` record.
"""
from __future__ import annotations

import os
import signal as signal_module

STOP_SIGNALS = ('SIGTERM', 'SIGINT')
EXIT_CLEAN, EXIT_FAULT, EXIT_REFUSED = 0, 1, 2


class Component:
    """Named start/close pair. ``start`` must release its own partial resources on failure."""

    def __init__(self, name, start, close):
        if not isinstance(name, str) or not name or not callable(start) or not callable(close):
            raise ValueError('Named component with start/close callables required')
        self.name, self._start, self._close = name, start, close

    def start(self):
        return self._start()

    def close(self):
        return self._close()


def install_signal_handlers(handler, names=STOP_SIGNALS, module=signal_module):
    """Install handlers on the main thread; returns a restore callable."""
    previous = {}
    for name in names:
        number = getattr(module, name)
        previous[number] = module.signal(number, handler)
    def restore():
        for number, old in previous.items():
            module.signal(number, old)
    return restore


class StationSupervisor:
    def __init__(self, journal, components, step, *, identity=None, install_signals=install_signal_handlers):
        names = [component.name for component in components]
        if not components or len(set(names)) != len(names):
            raise ValueError('Uniquely named ordered components required')
        if not callable(step):
            raise ValueError('Explicit simulation step callable required')
        self.journal, self.components, self.step = journal, list(components), step
        self.identity = dict(identity or {})
        self.install_signals = install_signals
        self.stop_reason = None
        self.stop_signal = None
        self.steps = 0

    def handle_signal(self, number, _frame=None):
        # Async-signal context: set flags only; the owner loop journals and stops.
        if self.stop_reason is None:
            try:
                self.stop_signal = signal_module.Signals(number).name
            except ValueError:
                self.stop_signal = str(number)
            self.stop_reason = 'signal'

    def request_stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason

    def run(self):
        restore = None
        started, failure = [], None
        self.journal.record('starting', pid=os.getpid(), components=[c.name for c in self.components],
                            **self.identity)
        try:
            if self.install_signals is not None:
                restore = self.install_signals(self.handle_signal)
            for component in self.components:
                if self.stop_reason is not None:
                    break  # Stop requested during startup: do not start the next service.
                component.start()
                started.append(component)
                self.journal.record('component_started', component=component.name)
            if len(started) == len(self.components) and self.stop_reason is None:
                self.journal.record('started', components=[c.name for c in started])
            while self.stop_reason is None:
                if self.step() is False:
                    self.request_stop('service_stopped')
                else:
                    self.steps += 1
        except Exception as error:
            failure = type(error).__name__+': '+str(error)
            self.request_stop('fault')
        finally:
            self.journal.record('stop_requested', reason=self.stop_reason, signal=self.stop_signal,
                                steps=self.steps, started=[c.name for c in started])
            order, errors = [], []
            for component in reversed(started):
                try:
                    result = component.close()
                    order.append(component.name)
                    self.journal.record('component_stopped', component=component.name,
                                        result=result if isinstance(result, dict) else None)
                except Exception as error:
                    errors.append(dict(component=component.name, error=type(error).__name__+': '+str(error)))
            if restore is not None:
                restore()
            code = EXIT_FAULT if failure or errors else EXIT_CLEAN
            self.journal.record('stopped', reason=self.stop_reason, signal=self.stop_signal, fault=failure,
                                shutdown_order=order, cleanup_errors=errors, steps=self.steps,
                                clean=code == EXIT_CLEAN, exit_code=code)
        return code

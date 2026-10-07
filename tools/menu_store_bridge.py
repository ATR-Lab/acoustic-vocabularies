"""Private DEMO-only selection adapter to the real #11 append-only store.

No bank generation, reserve substitution, relaxed validator, or network service.
An unresolved write-ahead intent admits only the identical request on recovery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sound/src'))
from av_sound._schemas import strict_loads
from av_sound.dyad_bank import load_dyad_bank
from av_sound.grammar import ATOM_IDS
from av_sound.package import load_package
from av_sound.store import VocabularyStore, CommitRejected, canonical_json, snapshot_digest

CONFIG_KEYS = {'schema_version', 'demo_only', 'unit_id', 'book_id', 'bank_path',
               'bank_file_sha256', 'bank_sha256', 'package_path', 'package_sha256', 'store_root'}
REQUEST_KEYS = {'schema_version', 'request_id', 'operation', 'unit_id', 'book_id',
                'bank_sha256', 'package_sha256', 'expected_head', 'expected_snapshot_sha256',
                'profile', 'menu_key', 'rank'}
RECEIPT_KEYS = {'schema_version','request_id','operation','unit_id','book_id','bank_sha256','package_sha256',
                'profile','menu_key','rank','request_sha256','config_sha256','status','accepted','reason',
                'before_head','after_head','before_snapshot_sha256','after_snapshot_sha256','pcm_sha256',
                'file_sha256','source_kind','participant_ready','receipt_sha256'}
HASH = re.compile(r'[0-9a-f]{64}\Z')
GUID = re.compile(r'[0-9a-f]{32}\Z')
ID = re.compile(r'DEMO-[A-Za-z0-9-]{1,58}\Z')
EMPTY_SNAPSHOT = snapshot_digest({})
RESPONSE_KEYS = {'schema_version','request_id','request_sha256','config_sha256',
                 'receipt','snapshot','error','response_sha256'}


class BridgeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise BridgeError(code)


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def read_json(path):
    return strict_loads(Path(path).read_bytes())


def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_new(path, value):
    path = Path(path)
    with path.open('xb') as stream:
        stream.write(canonical_json(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


@contextmanager
def store_lock(root):
    """All bridge instances lock the store, not just their individual book.

Other direct #11 writers must remain stopped; this does not retrofit locking
into the upstream API. OS ownership releases the lock after process death.
"""
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.menu-bridge.lock').open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise BridgeError('STORE_BUSY') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class MenuStoreBridge:
    def __init__(self, config):
        self.config = strict_loads(canonical_json(config))  # Detached immutable-by-owner input.
        c = self.config
        require(set(c) == CONFIG_KEYS and type(c['schema_version']) is int and c['schema_version'] == 1, 'CONFIG_SHAPE')
        require(c['demo_only'] is True and isinstance(c['unit_id'], str) and ID.fullmatch(c['unit_id'])
                and isinstance(c['book_id'], str) and ID.fullmatch(c['book_id']), 'QUALIFIED_BANK_UNAVAILABLE')
        require(all(isinstance(c[k], str) and HASH.fullmatch(c[k]) for k in
                    ('bank_file_sha256', 'bank_sha256', 'package_sha256')), 'CONFIG_PIN')
        require(all(isinstance(c[k], str) and c[k] for k in ('bank_path', 'package_path', 'store_root')), 'CONFIG_PATH')
        self.config_sha256 = digest(c)
        self.store = VocabularyStore(c['store_root'])  # Upstream reserved signals and threshold remain defaults.
        self.book_id = c['book_id']
        self.directory = self.store.book_dir(self.book_id)
        self.journal = self.directory / 'menu-bridge.jsonl'
        self.pending = self.directory / 'menu-bridge.pending.json'

    def _inputs(self):
        c = self.config
        require(digest(c) == self.config_sha256, 'CONFIG_CHANGED')
        require(hashlib.sha256(Path(c['bank_path']).read_bytes()).hexdigest() == c['bank_file_sha256'], 'BANK_FILE_HASH')
        bank = load_dyad_bank(c['bank_path'])
        require(bank.demo and bank.bank_sha256() == c['bank_sha256'], 'BANK_PIN')
        package = load_package(c['package_path'], expected_package_sha256=c['package_sha256'])
        require(package.demo and package.study == 'B' and package.package_id == bank.bank_id
                and package.combinations_checked == 1536, 'PACKAGE_BINDING')
        require(package.manifest['bank']['bank_sha256'] == c['bank_sha256'], 'PACKAGE_BANK_PIN')
        require({x['atom_id']: x['semantic_label'] for x in package.answers['atoms']} == dict(bank.labels), 'BANK_LABELS')
        options = {}
        for row in package.audio['options']:
            key = (row['profile'], row['atom_id'], row['rank'])
            option = bank.option(*key)
            require(row['pcm_sha256'] == option.pcm_sha256 and row['menu'] == option.menu, 'BANK_PACKAGE_OPTION')
            require(hashlib.sha256(package.pcm(row['path'])).hexdigest() == option.pcm_sha256, 'OPTION_PCM')
            options[key] = row
        require(len(options) == 192, 'OPTION_INVENTORY')
        return bank, package, options

    def _request(self, value, *, allow_verify=False):
        r = strict_loads(canonical_json(value))
        require(isinstance(r, dict) and set(r) == REQUEST_KEYS, 'REQUEST_SHAPE')
        require(type(r['schema_version']) is int and r['schema_version'] == 1 and
                isinstance(r['request_id'], str) and GUID.fullmatch(r['request_id']), 'REQUEST_ID')
        verify = allow_verify and r['operation'] == 'verify'
        require((verify or r['operation'] in ('profile', 'atom')) and
                (r['profile'] in ('P1', 'P2', 'P3') or verify and r['profile'] is None), 'REQUEST_OPERATION')
        for key in ('unit_id', 'book_id', 'bank_sha256', 'package_sha256'):
            require(r[key] == self.config[key], 'REQUEST_BINDING')
        require(isinstance(r['expected_snapshot_sha256'], str) and HASH.fullmatch(r['expected_snapshot_sha256']), 'SNAPSHOT_PIN')
        require(r['expected_head'] is None or isinstance(r['expected_head'], str) and HASH.fullmatch(r['expected_head']), 'HEAD_PIN')
        if verify:
            require(r['menu_key'] == 'verify' and r['rank'] is None, 'VERIFY_SHAPE')
        elif r['operation'] == 'profile':
            require(r['menu_key'] == 'profile' and r['rank'] is None, 'PROFILE_SHAPE')
        else:
            require(r['menu_key'] in ATOM_IDS and type(r['rank']) is int and r['rank'] in (1, 2, 3), 'ATOM_SHAPE')
        return r

    def _records(self):
        if not self.journal.exists():
            return []
        data = self.journal.read_bytes()
        require(not data or data.endswith(b'\n'), 'JOURNAL_TORN')
        records, previous, ids = [], '0' * 64, set()
        for raw in data.splitlines():
            row = strict_loads(raw)
            require(set(row) == {'request', 'receipt', 'after_snapshot', 'previous_sha256', 'record_sha256'}, 'JOURNAL_SHAPE')
            require(raw == canonical_json(row) and row['previous_sha256'] == previous and
                    row['record_sha256'] == digest({k:v for k,v in row.items() if k != 'record_sha256'}), 'JOURNAL_HASH')
            request = self._request(row['request']); receipt = row['receipt']
            require(request['operation'] == ('atom' if records else 'profile'), 'JOURNAL_OPERATION_ORDER')
            require(set(receipt) == RECEIPT_KEYS and all(receipt[k] == request[k] for k in
                    ('schema_version','request_id','operation','unit_id','book_id','bank_sha256','package_sha256','profile','menu_key','rank')),
                    'RECEIPT_BINDING')
            require(receipt['source_kind'] == 'synthetic' and receipt['participant_ready'] is False
                    and type(receipt['accepted']) is bool, 'RECEIPT_SCOPE')
            require(receipt['status'] in ('profile_selected','committed','rejected')
                    and receipt['accepted'] == (receipt['status'] != 'rejected')
                    and (receipt['status'] == 'profile_selected') == (request['operation'] == 'profile'), 'RECEIPT_STATUS')
            if receipt['status'] == 'committed':
                require(all(isinstance(receipt[k], str) and HASH.fullmatch(receipt[k]) for k in ('pcm_sha256','file_sha256'))
                        and receipt['reason'] is None, 'RECEIPT_CONTENT')
            else:
                require(receipt['pcm_sha256'] is None and receipt['file_sha256'] is None and
                        receipt['reason'] == ('E_REJECTED' if receipt['status'] == 'rejected' else None), 'RECEIPT_CONTENT')
            require(receipt['before_head'] == request['expected_head']
                    and receipt['before_snapshot_sha256'] == request['expected_snapshot_sha256'], 'RECEIPT_BEFORE')
            require(request['expected_head'] == (records[-1]['receipt']['after_head'] if records else None)
                    and request['expected_snapshot_sha256'] == (records[-1]['receipt']['after_snapshot_sha256'] if records else EMPTY_SNAPSHOT),
                    'JOURNAL_SEQUENCE')
            require(receipt['config_sha256'] == self.config_sha256 and receipt['request_sha256'] == digest(request)
                    and receipt['request_id'] == request['request_id'] and request['request_id'] not in ids, 'JOURNAL_BINDING')
            require(receipt['receipt_sha256'] == digest({k:v for k,v in receipt.items() if k != 'receipt_sha256'})
                    and receipt['after_snapshot_sha256'] == snapshot_digest(row['after_snapshot']), 'RECEIPT_HASH')
            ids.add(request['request_id']); records.append(row); previous = row['record_sha256']
        return records

    def _snapshot(self, expected_head=None):
        report = self.store.verify(self.book_id, rerender=True, expected_head=expected_head)
        require(report.ok, 'STORE_INTEGRITY')
        # The upstream anchor may be any earlier line; bridge state requires the
        # exact current head as well, rejecting unaccounted writes and truncation.
        if expected_head is not None:
            require(report.chain_head == expected_head, 'STORE_HEAD_CHANGED')
        snapshot = self.store.snapshot(self.book_id)
        for atom in snapshot:
            entry = self.store.get(self.book_id, atom)  # Re-read the actual stored blob.
            snapshot[atom].update(file_sha256=entry.file_sha256, n_samples=entry.n_samples)
            require(hashlib.sha256(entry.pcm).hexdigest() == entry.pcm_sha256, 'STORED_PCM_CHANGED')
        return snapshot, report.chain_head

    def _current(self, rows):
        if not rows:
            require(not self.store.log_path(self.book_id).exists(), 'UNTRACKED_BOOK')
            return {}, None
        latest = rows[-1]
        snapshot, head = self._snapshot(latest['receipt']['after_head'])
        require(snapshot == latest['after_snapshot'], 'OLD_ENTRY_CHANGED')
        return snapshot, head

    def _append(self, request, receipt, snapshot, rows):
        row = dict(request=request, receipt=receipt, after_snapshot=snapshot,
                   previous_sha256=rows[-1]['record_sha256'] if rows else '0'*64)
        row['record_sha256'] = digest(row)
        with self.journal.open('ab') as stream:
            stream.write(canonical_json(row) + b'\n'); stream.flush(); os.fsync(stream.fileno())
        sync_directory(self.directory)

    def _intent(self, request):
        raw = self.pending.read_bytes()
        intent = strict_loads(raw)
        require(isinstance(intent,dict) and set(intent) == {'request', 'before_snapshot', 'before_head', 'config_sha256', 'intent_sha256'}
                and raw == canonical_json(intent)+b'\n'
                and intent['intent_sha256'] == digest({k:v for k,v in intent.items() if k != 'intent_sha256'}), 'INTENT_HASH')
        require(intent['request'] == request and intent['config_sha256'] == self.config_sha256, 'UNRESOLVED_INTENT')
        require(intent['before_head'] == request['expected_head']
                and isinstance(intent['before_snapshot'],dict)
                and snapshot_digest(intent['before_snapshot']) == request['expected_snapshot_sha256'], 'INTENT_BASE')
        return intent

    def select(self, value):
        request = self._request(value)
        with store_lock(self.store.root):
            return self._select_locked(request)

    def _select_locked(self, request):
        bank, package, options = self._inputs()
        rows = self._records()
        previous_request = next((x for x in rows if x['request']['request_id'] == request['request_id']), None)
        if previous_request:
            require(previous_request['request'] == request, 'REQUEST_ID_CONFLICT')
            self._current(rows)
            if self.pending.exists():
                self._intent(request)
                require(previous_request is rows[-1], 'UNRESOLVED_INTENT')
                self.pending.unlink(); sync_directory(self.directory)
            return strict_loads(canonical_json(previous_request['receipt']))
        if self.pending.exists():
            intent = self._intent(request)
            before, before_head = intent['before_snapshot'], intent['before_head']
            require(before == (rows[-1]['after_snapshot'] if rows else {}) and
                    before_head == (rows[-1]['receipt']['after_head'] if rows else None), 'INTENT_BASE')
            require(request['expected_head'] == before_head and request['expected_snapshot_sha256'] == snapshot_digest(before), 'OLD_SNAPSHOT_PIN')
        else:
            before, before_head = self._current(rows)
            require(request['expected_head'] == before_head and request['expected_snapshot_sha256'] == snapshot_digest(before), 'OLD_SNAPSHOT_PIN')
            if request['operation'] == 'profile':
                require(not rows and before_head is None, 'PROFILE_ALREADY_SELECTED')
            else:
                require(rows and rows[0]['receipt']['profile'] == request['profile'], 'FIXED_PROFILE')
                require(request['menu_key'] not in before, 'ATOM_ALREADY_COMMITTED')
            self.directory.mkdir(parents=True, exist_ok=True)
            intent = dict(request=request, before_snapshot=before, before_head=before_head, config_sha256=self.config_sha256)
            intent['intent_sha256'] = digest(intent)
            write_new(self.pending, intent)
        reason, entry = None, None
        if request['operation'] == 'profile':
            if not self.store.log_path(self.book_id).exists():
                self.store.create_book(self.book_id, request['profile'], kind='synthetic')
            info = self.store.book(self.book_id)
            require(info.profile.value == request['profile'] and info.kind == 'synthetic' and info.n_records == 1
                    and info.n_entries == 0 and not info.frozen, 'PROFILE_RECOVERY_MISMATCH')
            status = 'profile_selected'
        else:
            current, current_head = self._snapshot()
            require(all(current.get(k) == v for k,v in before.items()), 'OLD_ENTRY_CHANGED')
            info = self.store.book(self.book_id)
            require(not info.frozen and info.profile.value == request['profile'], 'BOOK_NOT_SELECTABLE')
            atom = request['menu_key']; option = bank.option(request['profile'], atom, request['rank'])
            row = options[(request['profile'], atom, request['rank'])]
            source = 'menu-' + request['request_id']
            if current_head == before_head:
                require(current == before, 'OLD_ENTRY_CHANGED')
                try:
                    entry, _ = self.store.commit(self.book_id, atom, bank.labels[atom], option.recipe,
                                                source=source, profile=request['profile'], pcm_sha256=option.pcm_sha256)
                except CommitRejected as error:
                    reason = error.code  # Preserve actual upstream rejection; no alternate candidate.
            else:
                records = self.store.records(self.book_id)
                last = records[-1]
                require(last['event'] == 'commit' and last['source'] == source and last['atom_id'] == atom
                        and last['prev_sha256'] == before_head and set(current) == set(before) | {atom}, 'COMMIT_RECOVERY_MISMATCH')
                entry = self.store.get(self.book_id, atom)
            if entry is not None:
                require(entry.profile.value == request['profile'] and entry.recipe.sha256() == option.recipe.sha256()
                        and entry.semantic_label == bank.labels[atom] and entry.pcm_sha256 == option.pcm_sha256
                        and entry.file_sha256 == row['file_sha256'] and entry.pcm == package.pcm(row['path']), 'COMMITTED_SELECTION_MISMATCH')
            status = 'committed' if entry else 'rejected'
        after, after_head = self._snapshot()
        require(all(after.get(k) == v for k,v in before.items()), 'OLD_ENTRY_CHANGED')
        require(set(after) == (set(before) | {request['menu_key']} if entry else set(before)), 'UNEXPECTED_STORE_WRITE')
        if status == 'rejected':
            require(after_head == before_head and after == before, 'REJECTED_STORE_CHANGED')
        receipt = {k:request[k] for k in ('schema_version','request_id','operation','unit_id','book_id','bank_sha256','package_sha256','profile','menu_key','rank')}
        receipt.update(request_sha256=digest(request), config_sha256=self.config_sha256,
                       status=status, accepted=status != 'rejected', reason=reason,
                       before_head=before_head, after_head=after_head,
                       before_snapshot_sha256=snapshot_digest(before), after_snapshot_sha256=snapshot_digest(after),
                       pcm_sha256=entry.pcm_sha256 if entry else None, file_sha256=entry.file_sha256 if entry else None,
                       source_kind='synthetic', participant_ready=False)
        receipt['receipt_sha256'] = digest(receipt)
        self._append(request, receipt, after, rows)
        self.pending.unlink(); sync_directory(self.directory)
        return strict_loads(canonical_json(receipt))

    def verified_snapshot(self, *, expected_head, expected_snapshot_sha256):
        """Strict latest-head handoff for teaching, with no recipe or answer label."""
        with store_lock(self.store.root):
            return self._verified_snapshot_locked(expected_head, expected_snapshot_sha256)

    def _verified_snapshot_locked(self, expected_head, expected_snapshot_sha256):
        bank, package, options = self._inputs()
        require(not self.pending.exists(), 'UNRESOLVED_INTENT')
        rows = self._records(); snapshot, head = self._current(rows)
        require(head == expected_head and snapshot_digest(snapshot) == expected_snapshot_sha256, 'READ_SNAPSHOT_PIN')
        for x in rows:
            receipt = x['receipt']
            if receipt['status'] != 'committed':
                continue
            atom = receipt['menu_key']
            actual = self.store.get(self.book_id, atom)
            option = bank.option(receipt['profile'], atom, receipt['rank'])
            packaged = options[(receipt['profile'], atom, receipt['rank'])]
            require(actual.recipe.sha256() == option.recipe.sha256()
                    and actual.pcm_sha256 == receipt['pcm_sha256'] == option.pcm_sha256
                    and actual.file_sha256 == receipt['file_sha256'] == packaged['file_sha256']
                    and actual.profile.value == receipt['profile'] and actual.semantic_label == bank.labels[atom], 'READ_SELECTION_BINDING')
        result = {k:self.config[k] for k in ('schema_version','unit_id','book_id','bank_sha256','package_sha256')}
        result.update(config_sha256=self.config_sha256, profile=rows[0]['receipt']['profile'] if rows else None,
                      profile_selection_receipt_sha256=rows[0]['receipt']['receipt_sha256'] if rows else None,
                      book_head=head, snapshot_sha256=snapshot_digest(snapshot),
                      journal_head=rows[-1]['record_sha256'] if rows else None,
                      source_kind='synthetic', participant_ready=False,
                      entries=[dict(atom_id=x['receipt']['menu_key'], profile=x['receipt']['profile'],
                                    rank=x['receipt']['rank'], pcm_sha256=x['receipt']['pcm_sha256'],
                                    file_sha256=x['receipt']['file_sha256'], selection_receipt_sha256=x['receipt']['receipt_sha256'])
                               for x in rows if x['receipt']['status'] == 'committed'])
        require(len(result['entries']) == len(snapshot), 'SELECTION_INVENTORY')
        result['manifest_sha256'] = digest(result)
        return strict_loads(canonical_json(result))


def _local_path(path, *, directory=False):
    """Private local mailbox paths cannot redirect through links/reparse points."""
    path = Path(os.path.abspath(path))
    require(not str(path).startswith('\\\\'), 'MAILBOX_LOCAL_REQUIRED')
    for item in (*reversed(path.parents), path):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and
                not getattr(info, 'st_file_attributes', 0) & 0x400, 'MAILBOX_LINK')
    if path.exists():
        require(path.is_dir() if directory else path.is_file(), 'MAILBOX_PATH_KIND')
    return path


def _atomic_new(path, value):
    """Publish a fully fsynced new response; never replace prior evidence."""
    path = _local_path(path)
    temporary = path.with_name('.' + uuid.uuid4().hex + '.tmp')
    write_new(temporary, value)
    try:
        os.link(temporary, path)  # Atomic, no-replace on the supported local filesystem.
        sync_directory(path.parent)
    finally:
        temporary.unlink()
        sync_directory(path.parent)


def _mailbox_request(path):
    _local_path(path)
    with path.open('rb') as stream:
        raw = stream.read(16385)
    require(0 < len(raw) <= 16384, 'MAILBOX_REQUEST_SIZE')
    try:
        value = strict_loads(raw)
        request_sha256 = digest(value)
    except (ValueError, TypeError, UnicodeError) as error:
        # Without a canonical request, no correlated receipt can be issued.
        raise BridgeError('MAILBOX_REQUEST_JSON') from error
    return value, request_sha256


def serve_mailbox(bridge, mailbox, *, seconds=300, max_requests=128):
    """Bounded, local-file transport. Caller provisions the private directory ACL.

The store and mailbox locks cover the whole lifetime. Atomic client publication
and one outstanding request are required. Requests and responses remain as audit
evidence; a request with an existing valid response is never executed again.
"""
    require(type(seconds) in (int,float) and math.isfinite(seconds) and 0 < seconds <= 3600,
            'MAILBOX_DURATION')
    require(type(max_requests) is int and 1 <= max_requests <= 1024, 'MAILBOX_LIMIT')
    root = _local_path(mailbox, directory=True)
    require(root != bridge.store.root.absolute(), 'MAILBOX_STORE_SEPARATE')
    root.mkdir(parents=True, exist_ok=True)
    requests = _local_path(root/'requests', directory=True)
    responses = _local_path(root/'responses', directory=True)
    requests.mkdir(exist_ok=True); responses.mkdir(exist_ok=True)
    _local_path(root/'.menu-bridge.lock')
    _local_path(bridge.store.root/'.menu-bridge.lock')
    processed = 0
    started = time.monotonic()
    with store_lock(bridge.store.root), store_lock(root):
        while processed < max_requests and time.monotonic()-started < seconds:
            pending = []
            request_ids = set()
            for path in sorted(requests.iterdir()):
                if re.fullmatch(r'\.[0-9a-f]{32}\.tmp', path.name):
                    _local_path(path)
                    continue
                require(re.fullmatch(r'[0-9a-f]{32}\.json', path.name), 'MAILBOX_FILENAME')
                value, request_hash = _mailbox_request(path)
                request_ids.add(path.stem)
                response_path = responses/path.name
                if response_path.exists():
                    _local_path(response_path)
                    with response_path.open('rb') as stream:
                        raw = stream.read(131073)
                    require(len(raw) <= 131072, 'MAILBOX_RESPONSE_SIZE')
                    response = strict_loads(raw)
                    require(isinstance(response,dict) and set(response) == RESPONSE_KEYS
                            and raw == canonical_json(response)+b'\n'
                            and response['schema_version'] == 1
                            and response['request_id'] == path.stem
                            and response['request_sha256'] == request_hash
                            and response['config_sha256'] == bridge.config_sha256
                            and response['response_sha256'] == digest({k:v for k,v in response.items() if k!='response_sha256'}),
                            'MAILBOX_RESPONSE_CHANGED')
                else:
                    pending.append((path, value, request_hash))
            for path in responses.iterdir():
                if re.fullmatch(r'\.[0-9a-f]{32}\.tmp', path.name):
                    _local_path(path)
                    continue
                require(re.fullmatch(r'[0-9a-f]{32}\.json', path.name)
                        and path.stem in request_ids, 'MAILBOX_ORPHAN_RESPONSE')
                _local_path(path)
            require(len(pending) <= 1, 'MAILBOX_MULTIPLE_PENDING')
            if not pending:
                time.sleep(min(.05, max(0, seconds-(time.monotonic()-started))))
                continue
            path, value, request_hash = pending[0]
            response = dict(schema_version=1, request_id=path.stem, request_sha256=request_hash,
                            config_sha256=bridge.config_sha256, receipt=None, snapshot=None, error=None)
            try:
                request = bridge._request(value, allow_verify=True)
                require(request['request_id'] == path.stem, 'MAILBOX_REQUEST_ID')
                if request['operation'] == 'verify':
                    snapshot = bridge._verified_snapshot_locked(request['expected_head'], request['expected_snapshot_sha256'])
                    require(snapshot['profile'] == request['profile'], 'READ_PROFILE_PIN')
                else:
                    receipt = bridge._select_locked(request)
                    snapshot = bridge._verified_snapshot_locked(receipt['after_head'], receipt['after_snapshot_sha256'])
                    response['receipt'] = receipt
                response['snapshot'] = snapshot
            except Exception as error:
                # Keep paths, semantic labels and upstream diagnostic text private.
                response.update(receipt=None, snapshot=None,
                                error=error.code if isinstance(error,BridgeError) else 'INTEGRITY_OR_IO_ERROR')
            # Detect a changed request before publishing, even if the commit is now
            # uncertain. The durable intent/journal handles an identical retry.
            require(_mailbox_request(path)[1] == request_hash, 'MAILBOX_REQUEST_CHANGED')
            response['response_sha256'] = digest(response)
            _atomic_new(responses/path.name, response)
            processed += 1
    return dict(processed=processed, elapsed_seconds=time.monotonic()-started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True, help='Independently provisioned raw config byte hash')
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--request', type=Path)
    operation.add_argument('--read-verified', action='store_true')
    operation.add_argument('--serve-mailbox', type=Path)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--max-requests', type=int, default=128)
    parser.add_argument('--expected-head')
    parser.add_argument('--expected-snapshot-sha256')
    parser.add_argument('--receipt', '--output', dest='output', type=Path)
    args = parser.parse_args()
    require(args.output is None if args.serve_mailbox else args.output is not None, 'CLI_OUTPUT')
    require(args.output is None or not args.output.exists(), 'RECEIPT_EXISTS')
    raw = args.config.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == args.config_sha256, 'CONFIG_FILE_HASH')
    bridge = MenuStoreBridge(strict_loads(raw))
    if args.serve_mailbox:
        require(args.expected_head is None and args.expected_snapshot_sha256 is None, 'UNEXPECTED_CLI_PINS')
        print(json.dumps(serve_mailbox(bridge, args.serve_mailbox, seconds=args.seconds, max_requests=args.max_requests)))
        return 0
    if args.read_verified:
        require(isinstance(args.expected_head,str) and (args.expected_head == 'none' or HASH.fullmatch(args.expected_head))
                and isinstance(args.expected_snapshot_sha256,str) and HASH.fullmatch(args.expected_snapshot_sha256), 'READ_PINS_REQUIRED')
        result = bridge.verified_snapshot(expected_head=None if args.expected_head == 'none' else args.expected_head,
                                          expected_snapshot_sha256=args.expected_snapshot_sha256)
        write_new(args.output, result)
        print(json.dumps({'status':'verified', 'manifest_sha256':result['manifest_sha256']}))
        return 0
    require(args.expected_head is None and args.expected_snapshot_sha256 is None, 'UNEXPECTED_CLI_PINS')
    result = bridge.select(read_json(args.request))
    write_new(args.output, result)
    print(json.dumps({'status': result['status'], 'receipt_sha256': result['receipt_sha256']}))
    return 0 if result['accepted'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BridgeError as error:
        print(json.dumps({'error': error.code}), file=sys.stderr)
        raise SystemExit(1)

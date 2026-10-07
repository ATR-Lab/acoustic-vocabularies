"""Explicit digest/limit-only inputs; no hidden trial tuple enters the source."""
import math

from .observations import validate_options


def add_arguments(parser):
    parser.add_argument('--view-observation-plan-sha256')
    parser.add_argument('--view-observation-source-commit')
    parser.add_argument('--view-observation-max-records',type=int)
    parser.add_argument('--view-observation-max-bytes',type=int)
    parser.add_argument('--view-observation-max-seconds',type=float)


def profile_options(args):
    values={key:getattr(args,attribute) for key,attribute in {
        'plan_sha256':'view_observation_plan_sha256',
        'source_commit':'view_observation_source_commit',
        'max_records':'view_observation_max_records',
        'max_serialized_bytes':'view_observation_max_bytes',
        'max_seconds':'view_observation_max_seconds'}.items()}
    if all(value is None for value in values.values()):
        return None
    if any(value is None for value in values.values()):
        raise ValueError('All observation digest/limit arguments are explicit')
    if (not args.reset_check or not args.skip_reach or not math.isfinite(args.e2e_seconds)
            or not 5 <= args.e2e_seconds <= 3600
            or any((args.publisher_seconds,args.command_check,args.published_command_check,
                    args.disconnect_check,args.demo_check,args.demo_preflight,args.grip_check,
                    args.protected_stream_seconds,args.same_iteration_check,args.e2e_private_timing_seconds))
            or getattr(args,'e2e_handle_cache',False)):
        raise ValueError('Observation requires only the bounded ordinary joined source')
    validate_options(values,args.e2e_seconds)
    return values

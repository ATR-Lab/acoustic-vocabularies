"""Message composer: action motif + 200 ms digital silence + referent motif (#10).

The byte contract (`sound/docs/composition.md`): a message is the action atom's
int16 LE samples, then exactly 9,600 zero samples, then the referent atom's
samples. Both atoms share one family and one profile. With motifs of 450-900 ms a
message has 52,800-96,000 samples (1.1-2.0 s) by construction.

Held-out messages never exist as complete audio (Protocol constants):
`compose_message` and `write_message_wav` refuse them, while `composite_hash` and
`message_length` work for every legal message because they return no samples.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, NoReturn, Protocol

from av_sound.grammar import HELDOUT_MESSAGE_IDS, MessageRef, parse_atom_id, parse_message_id
from av_sound.recipe import TOTAL_MS, Profile, Recipe
from av_sound.renderer import Rendered
from av_sound.tables import SAMPLE_RATE, SAMPLES_PER_MS
from av_sound.wav import write_wav

logger = logging.getLogger(__name__)

GAP_MS: Final = 200
GAP_SAMPLES: Final = GAP_MS * SAMPLES_PER_MS
"""Digital silence between action and referent: 9,600 zero samples (19,200 bytes)."""
MOTIF_SAMPLES: Final[tuple[int, ...]] = tuple(t * SAMPLES_PER_MS for t in TOTAL_MS)
"""Legal motif lengths: 21,600, 28,800, 36,000 or 43,200 samples."""
MIN_MESSAGE_SAMPLES: Final = 2 * MOTIF_SAMPLES[0] + GAP_SAMPLES
"""52,800 samples (1.1 s)."""
MAX_MESSAGE_SAMPLES: Final = 2 * MOTIF_SAMPLES[-1] + GAP_SAMPLES
"""96,000 samples (2.0 s)."""

_GAP_BYTES: Final = bytes(2 * GAP_SAMPLES)
_DEFAULT_HELDOUT: Final = frozenset(HELDOUT_MESSAGE_IDS)

E_ROLE_ORDER = "E_ROLE_ORDER"
E_FAMILY_MISMATCH = "E_FAMILY_MISMATCH"
E_PROFILE_MISMATCH = "E_PROFILE_MISMATCH"
E_MOTIF_LENGTH = "E_MOTIF_LENGTH"
E_HELDOUT = "E_HELDOUT"
E_INTEGRITY = "E_INTEGRITY"

AuditHook = Callable[[Mapping[str, str]], None]
"""Called with one event dict before a held-out refusal is raised."""


class CompositionError(ValueError):
    """Two atoms cannot form a message; `.code` names the failed check."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HeldOutMessageError(CompositionError):
    """The message is held out: it must never exist as complete audio."""

    def __init__(self, message_id: str) -> None:
        super().__init__(
            E_HELDOUT,
            f"{message_id} is held out: it never exists as complete audio "
            "(use composite_hash for its expected hash)",
        )
        self.message_id = message_id


class AtomAudioLike(Protocol):
    """Anything with an atom ID, a profile and int16 LE samples (e.g. a store entry)."""

    @property
    def atom_id(self) -> str: ...

    @property
    def profile(self) -> Profile | str: ...

    @property
    def pcm(self) -> bytes: ...


class _HasNSamples(Protocol):
    @property
    def n_samples(self) -> int: ...


class _HasRecipe(Protocol):
    @property
    def recipe(self) -> Recipe: ...


MotifMetadata = int | Recipe | Mapping[str, Any] | _HasNSamples | _HasRecipe | AtomAudioLike
"""`message_length` input: `total_ms`, a recipe, or an object with `n_samples`,
`recipe` or `pcm` (checked in that order)."""


@dataclass(frozen=True, slots=True)
class AtomAudio:
    """One committed atom: its ID (`K-a1` .. `Q-r4`), profile and samples."""

    atom_id: str
    profile: Profile
    pcm: bytes = field(repr=False)

    def __post_init__(self) -> None:
        parse_atom_id(self.atom_id)
        object.__setattr__(self, "profile", Profile(self.profile))
        pcm = self.pcm
        if isinstance(pcm, bytearray | memoryview):
            pcm = bytes(pcm)
        if not isinstance(pcm, bytes):
            raise TypeError(f"{self.atom_id}: pcm must be bytes, got {type(pcm).__name__}")
        if len(pcm) % 2:
            raise ValueError(f"{self.atom_id}: PCM byte length must be even (int16 samples)")
        object.__setattr__(self, "pcm", pcm)

    @classmethod
    def from_rendered(cls, atom_id: str, rendered: Rendered) -> AtomAudio:
        """Wrap a render result. Raises `OverflowError` if the motif overflowed."""
        return cls(atom_id, rendered.profile, rendered.pcm)

    @property
    def n_samples(self) -> int:
        return len(self.pcm) // 2

    @property
    def pcm_sha256(self) -> str:
        return hashlib.sha256(self.pcm).hexdigest()


@dataclass(frozen=True, slots=True)
class Message:
    """A composed trained message. Only `compose_message` should create one."""

    message_id: str
    profile: Profile
    action_id: str
    referent_id: str
    action_samples: int
    referent_samples: int
    action_pcm_sha256: str
    referent_pcm_sha256: str
    pcm: bytes = field(repr=False)
    pcm_sha256: str
    n_samples: int

    @property
    def duration_s(self) -> float:
        """Length in seconds (1.1 to 2.0)."""
        return self.n_samples / SAMPLE_RATE

    @property
    def referent_onset(self) -> int:
        """Sample index where the referent starts: action samples + 9,600."""
        return self.action_samples + GAP_SAMPLES


def _heldout_ids(heldout: Iterable[str] | None) -> frozenset[str]:
    if heldout is None:
        return _DEFAULT_HELDOUT
    if isinstance(heldout, str | bytes | Mapping):
        raise TypeError("heldout must be a collection of message IDs, e.g. a set or a tuple")
    ids = frozenset(heldout)
    for value in sorted(ids, key=repr):
        parse_message_id(value)
    return ids


def _message_ref(action: AtomAudioLike, referent: AtomAudioLike) -> MessageRef:
    a = parse_atom_id(action.atom_id)
    r = parse_atom_id(referent.atom_id)
    if a.role != "action" or r.role != "referent":
        raise CompositionError(
            E_ROLE_ORDER,
            f"a message is an action then a referent; got {a.atom_id} ({a.role}) "
            f"then {r.atom_id} ({r.role})",
        )
    if a.family != r.family:
        raise CompositionError(
            E_FAMILY_MISMATCH, f"{a.atom_id} and {r.atom_id} belong to different families"
        )
    return MessageRef(a.family, a.index, r.index)


def _common_profile(action: AtomAudioLike, referent: AtomAudioLike) -> Profile:
    a, r = Profile(action.profile), Profile(referent.profile)
    if a != r:
        raise CompositionError(
            E_PROFILE_MISMATCH,
            f"{action.atom_id} is {a.value} but {referent.atom_id} is {r.value}; "
            "a message uses one profile",
        )
    return a


def _check_motif_samples(n: int, what: str) -> int:
    if n not in MOTIF_SAMPLES:
        raise CompositionError(
            E_MOTIF_LENGTH,
            f"{what}: {n} samples; a motif has one of {list(MOTIF_SAMPLES)} (total_ms x 48)",
        )
    return n


def _motif_pcm(atom: AtomAudioLike) -> bytes:
    pcm = atom.pcm
    if not isinstance(pcm, bytes):
        raise TypeError(f"{atom.atom_id}: pcm must be bytes, got {type(pcm).__name__}")
    if len(pcm) % 2:
        raise CompositionError(E_MOTIF_LENGTH, f"{atom.atom_id}: odd PCM byte length")
    _check_motif_samples(len(pcm) // 2, atom.atom_id)
    return pcm


def _refuse(operation: str, ref: MessageRef, audit: AuditHook | None) -> NoReturn:
    event = {
        "event": "heldout_refused",
        "operation": operation,
        "message_id": ref.message_id,
        "action_id": ref.action.atom_id,
        "referent_id": ref.referent.atom_id,
    }
    logger.warning(
        "%s refused held-out message %s: held-out messages never exist as complete audio",
        operation,
        ref.message_id,
    )
    if audit is not None:
        audit(event)
    raise HeldOutMessageError(ref.message_id)


def compose_message(
    action: AtomAudioLike,
    referent: AtomAudioLike,
    *,
    heldout: Iterable[str] | None = None,
    audit: AuditHook | None = None,
) -> Message:
    """Compose action + 9,600 zero samples + referent for a trained message.

    Checks, in order: roles are action then referent (`E_ROLE_ORDER`), one family
    (`E_FAMILY_MISMATCH`), the message is not held out (`HeldOutMessageError`), one
    profile (`E_PROFILE_MISMATCH`) and legal motif lengths (`E_MOTIF_LENGTH`).

    `heldout` is the set of held-out message IDs (the curriculum status table); the
    default is the 14 held-out IDs of the fixed matrix. A refusal is logged on the
    `av_sound.composer` logger and passed to `audit`; no samples are concatenated
    and nothing is written.
    """
    heldout_ids = _heldout_ids(heldout)
    ref = _message_ref(action, referent)
    if ref.message_id in heldout_ids:
        _refuse("compose_message", ref, audit)
    profile = _common_profile(action, referent)
    a_pcm, r_pcm = _motif_pcm(action), _motif_pcm(referent)
    pcm = b"".join((a_pcm, _GAP_BYTES, r_pcm))
    return Message(
        message_id=ref.message_id,
        profile=profile,
        action_id=ref.action.atom_id,
        referent_id=ref.referent.atom_id,
        action_samples=len(a_pcm) // 2,
        referent_samples=len(r_pcm) // 2,
        action_pcm_sha256=hashlib.sha256(a_pcm).hexdigest(),
        referent_pcm_sha256=hashlib.sha256(r_pcm).hexdigest(),
        pcm=pcm,
        pcm_sha256=hashlib.sha256(pcm).hexdigest(),
        n_samples=len(pcm) // 2,
    )


compose = compose_message
"""Alias of `compose_message`."""


def composite_hash(action: AtomAudioLike, referent: AtomAudioLike) -> str:
    """SHA-256 of action + 19,200 zero bytes + referent, without building the message.

    Same role, family, profile and length checks as `compose_message`, but allowed
    for held-out messages: it hashes incrementally, returns no samples and writes
    nothing. This is the expected hash in the hidden-answer manifest (#13).
    """
    _message_ref(action, referent)
    _common_profile(action, referent)
    digest = hashlib.sha256()
    digest.update(_motif_pcm(action))
    digest.update(_GAP_BYTES)
    digest.update(_motif_pcm(referent))
    return digest.hexdigest()


def _motif_samples(motif: MotifMetadata) -> int:
    if isinstance(motif, bool):
        raise TypeError("expected total_ms, a recipe or an atom, got a bool")
    if isinstance(motif, int):
        if motif not in TOTAL_MS:
            raise CompositionError(
                E_MOTIF_LENGTH, f"total_ms {motif} is not one of {list(TOTAL_MS)}"
            )
        return motif * SAMPLES_PER_MS
    if isinstance(motif, Recipe):
        return motif.total_ms * SAMPLES_PER_MS
    if isinstance(motif, Mapping):
        return Recipe.from_dict(motif).total_ms * SAMPLES_PER_MS
    n_samples = getattr(motif, "n_samples", None)
    if isinstance(n_samples, int) and not isinstance(n_samples, bool):
        return _check_motif_samples(n_samples, repr(motif))
    recipe = getattr(motif, "recipe", None)
    if isinstance(recipe, Recipe | Mapping):
        return _motif_samples(recipe)
    pcm = getattr(motif, "pcm", None)
    if isinstance(pcm, bytes) and len(pcm) % 2 == 0:
        return _check_motif_samples(len(pcm) // 2, repr(motif))
    raise TypeError(
        f"cannot read a motif length from {type(motif).__name__}; pass total_ms, "
        "a Recipe, or an object with n_samples, recipe or pcm"
    )


def message_length(action: MotifMetadata, referent: MotifMetadata) -> int:
    """Message length in samples from metadata only: never renders or plays audio.

    Each argument is a `total_ms` value, a `Recipe` (or recipe dict), or an object
    with `n_samples`, `recipe` or `pcm` (a `Rendered`, an `AtomAudio`, a store
    entry). The result is `n_action + 9,600 + n_referent`, 52,800 to 96,000.
    """
    return _motif_samples(action) + GAP_SAMPLES + _motif_samples(referent)


def write_message_wav(
    message: Message,
    path: str | os.PathLike[str],
    *,
    heldout: Iterable[str] | None = None,
    audit: AuditHook | None = None,
) -> str:
    """Write a trained message as a canonical WAV file and return its `file_sha256`.

    Refuses a held-out message ID (same `heldout` and `audit` rules as
    `compose_message`) and a message whose samples do not match its hash.
    """
    heldout_ids = _heldout_ids(heldout)
    ref = parse_message_id(message.message_id)
    if ref.message_id in heldout_ids:
        _refuse("write_message_wav", ref, audit)
    n = len(message.pcm) // 2
    if (
        hashlib.sha256(message.pcm).hexdigest() != message.pcm_sha256
        or n != message.n_samples
        or not MIN_MESSAGE_SAMPLES <= n <= MAX_MESSAGE_SAMPLES
    ):
        raise CompositionError(
            E_INTEGRITY, f"{message.message_id}: samples do not match the message record"
        )
    return write_wav(message.pcm, path)

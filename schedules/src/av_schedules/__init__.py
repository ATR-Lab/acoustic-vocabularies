"""Curriculum, permutation and schedule generators for the acoustic-vocabulary studies.

Modules: ``matrix`` (abstract matrix and derived structures), ``seeds`` (seed derivation
and the portable random stream), ``latin`` (seeded Latin squares), ``design`` (Study A
batch table, Study B design table), ``curriculum`` (per-unit tables and permutation
documents), ``balance`` (balance report), ``output`` (rendering and writing), ``planning``
(oracle constants and the external planning-materials check), ``orders`` (per-person
visit schedules and the speech list), ``schedule_output`` (schedule files), ``assign``
and ``assign_output`` (allocation lists), ``reveal`` (reveal-next stub), ``masking``
(method string scan), ``cli``.
"""

__version__ = "0.1.0"

from .assign import (
    AAllocation,
    BAllocation,
    allocation_seed,
    build_a_allocation,
    build_b_allocation,
    check_a_allocation,
    check_b_allocation,
)
from .assign_output import assign_files, load_list
from .balance import BalanceRow, balance_csv, balance_rows, max_abs_deviation
from .curriculum import (
    CURRICULUM_COLUMNS,
    curriculum_csv,
    curriculum_rows,
    novel_by_visit,
    permutation_document,
    permutation_json,
)
from .design import (
    ABatch,
    BDyadSlot,
    Permutation,
    SetName,
    Unit,
    build_a_batch_table,
    build_b_design_table,
    build_units,
)
from .masking import find_method_strings
from .matrix import (
    FAMILIES,
    HELDOUT_SETS,
    INDICES,
    LABELS,
    MATRIX,
    ROLES,
    Cell,
    atom_id,
    cells,
    heldout_cells,
    index_waves,
    message_id,
    novel_visit,
    trained_cells,
    wave_atoms,
)
from .orders import (
    BlockPlan,
    SpeechCommand,
    assessment_counts,
    build_visit_schedule,
    check_visit_schedule,
    person_ids,
    speech_commands,
    speech_list_document,
    study_visits,
    unit_schedules,
    visit_plan,
    visit_schedule_json,
)
from .output import generate, render_set, table_csv, write_files
from .planning import check_planning
from .reveal import RevealError, RevealLog
from .schedule_output import generate_schedules, render_schedules
from .seeds import MasterSeed, SeedStream, demo_seed, derive_seed, load_master_seed, private_seed

__all__ = [
    "CURRICULUM_COLUMNS",
    "FAMILIES",
    "HELDOUT_SETS",
    "INDICES",
    "LABELS",
    "MATRIX",
    "ROLES",
    "AAllocation",
    "ABatch",
    "BAllocation",
    "BDyadSlot",
    "BalanceRow",
    "BlockPlan",
    "Cell",
    "MasterSeed",
    "Permutation",
    "RevealError",
    "RevealLog",
    "SeedStream",
    "SetName",
    "SpeechCommand",
    "Unit",
    "__version__",
    "allocation_seed",
    "assessment_counts",
    "assign_files",
    "atom_id",
    "balance_csv",
    "balance_rows",
    "build_a_allocation",
    "build_a_batch_table",
    "build_b_allocation",
    "build_b_design_table",
    "build_units",
    "build_visit_schedule",
    "cells",
    "check_a_allocation",
    "check_b_allocation",
    "check_planning",
    "check_visit_schedule",
    "curriculum_csv",
    "curriculum_rows",
    "demo_seed",
    "derive_seed",
    "find_method_strings",
    "generate",
    "generate_schedules",
    "heldout_cells",
    "index_waves",
    "load_list",
    "load_master_seed",
    "max_abs_deviation",
    "message_id",
    "novel_by_visit",
    "novel_visit",
    "permutation_document",
    "permutation_json",
    "person_ids",
    "private_seed",
    "render_schedules",
    "render_set",
    "speech_commands",
    "speech_list_document",
    "study_visits",
    "table_csv",
    "trained_cells",
    "unit_schedules",
    "visit_plan",
    "visit_schedule_json",
    "wave_atoms",
    "write_files",
]

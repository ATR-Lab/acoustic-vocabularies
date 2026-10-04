"""Curriculum, permutation and schedule generators for the acoustic-vocabulary studies.

Modules: ``matrix`` (abstract matrix and derived structures), ``seeds`` (seed derivation
and the portable random stream), ``latin`` (seeded Latin squares), ``design`` (Study A
batch table, Study B design table), ``curriculum`` (per-unit tables and permutation
documents), ``balance`` (balance report), ``output`` (rendering and writing), ``planning``
(oracle constants and the external planning-materials check), ``cli``.
"""

__version__ = "0.1.0"

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
from .output import generate, render_set, table_csv, write_files
from .planning import check_planning
from .seeds import MasterSeed, SeedStream, demo_seed, derive_seed, load_master_seed, private_seed

__all__ = [
    "CURRICULUM_COLUMNS",
    "FAMILIES",
    "HELDOUT_SETS",
    "INDICES",
    "LABELS",
    "MATRIX",
    "ROLES",
    "ABatch",
    "BDyadSlot",
    "BalanceRow",
    "Cell",
    "MasterSeed",
    "Permutation",
    "SeedStream",
    "SetName",
    "Unit",
    "__version__",
    "atom_id",
    "balance_csv",
    "balance_rows",
    "build_a_batch_table",
    "build_b_design_table",
    "build_units",
    "cells",
    "check_planning",
    "curriculum_csv",
    "curriculum_rows",
    "demo_seed",
    "derive_seed",
    "generate",
    "heldout_cells",
    "index_waves",
    "load_master_seed",
    "max_abs_deviation",
    "message_id",
    "novel_by_visit",
    "novel_visit",
    "permutation_document",
    "permutation_json",
    "private_seed",
    "render_set",
    "table_csv",
    "trained_cells",
    "wave_atoms",
    "write_files",
]

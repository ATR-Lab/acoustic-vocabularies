"""Reconciliation, prespecified analysis and integrity monitoring for the two studies.

The package is built by three issues on one shared skeleton (``docs/architecture.md``):

* Shared contracts (skeleton, implemented): ``templates`` (methodology log templates),
  ``vocab`` (value vocabularies and thresholds), ``codes`` (reconciliation checks and
  discrepancy codes), ``windows`` (visit windows), ``derived`` (reconciled and derived
  table specifications), ``schemas`` (published JSON Schemas), ``paths`` (data roots and
  the SYNTHETIC/REAL watermark), ``masking`` (outcome and condition field deny lists),
  ``fileio`` (canonical bytes and hashes), ``seeds`` (seeded generators), ``cli``.
* Reconciliation (#33): ``loaders``, ``references``, ``reconcile``, ``ledger``,
  ``derive``, ``synthetic_logs``.
* Analysis pipeline (#34): ``scoring``, ``unmask``, ``estimators``, ``missingness``,
  ``glmm``, ``rbridge``, ``simulate``, ``report``, ``pipeline``.
* Integrity dashboard (#35): ``monitoring``.

Import submodules directly; this module re-exports nothing but the version.
"""

__version__ = "0.1.0"

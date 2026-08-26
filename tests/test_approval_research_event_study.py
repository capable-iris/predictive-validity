import sys
from pathlib import Path

import numpy as np

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "analyses"
        / "approval-evidence-effect"
    ),
)

from approval_research_event_study import (  # noqa: E402
    estimate_dynamic,
    summarize_dynamic,
)


YEARS = np.arange(2016, 2026)
FIRST_TRIAL_YEAR = np.array([2017, 2017, 2017, 2017])
FIRST_APPROVAL_YEAR = np.array([2019, 2019, 9999, 9999])


def test_parallel_changes_have_zero_effect():
    outcome = np.tile(np.arange(len(YEARS), dtype=float), (4, 1))

    dynamic = estimate_dynamic(
        outcome,
        YEARS,
        FIRST_TRIAL_YEAR,
        FIRST_APPROVAL_YEAR,
        "not_yet_approved",
    )

    assert all(np.isclose(estimate.raw_att, 0.0) for estimate in dynamic.values())


def test_post_approval_increment_is_recovered():
    outcome = np.zeros((4, len(YEARS)))
    for year in (2020, 2021, 2022):
        outcome[:2, np.flatnonzero(YEARS == year)[0]] = 2.0

    dynamic = estimate_dynamic(
        outcome,
        YEARS,
        FIRST_TRIAL_YEAR,
        FIRST_APPROVAL_YEAR,
        "not_yet_approved",
    )
    summary = summarize_dynamic(dynamic)

    assert np.isclose(summary["mean_annual_raw_att_years_1_to_3"], 2.0)
    assert np.isclose(summary["cumulative_excess_records_years_1_to_3"], 6.0)

"""Project regression for the non-blocking summary classification finding.
Copy into backend/tests/ and use the project's existing environment.
This file was syntax-checked, NOT run inside a full project by the reviewer.
The identical scenario was executed in run_review.py's excerpt-based harness.
"""
from itertools import permutations

from app.schemas import AssistantTaskConstraint
from app.services.goal import plan_task_constraint_revision


def test_hard_add_and_hard_replace_to_new_content_have_same_summary_category():
    old = [{
        'id': 'sc-old-a', 'subject_type': 'teacher', 'subject_ids': ['T01'],
        'slot_business_ids': ['S1'], 'source_text': '张老师周一晚尽量别排',
    }]
    common = {
        'source_text': '张老师周二晚不能上',
        'subject_type': 'teacher', 'subject_ids': ['T01'],
        'slot_business_ids': ['S2'], 'hardness': 'hard',
    }
    actions = [
        AssistantTaskConstraint(id='tc-add', op='add', **common),
        AssistantTaskConstraint(id='tc-replace', op='replace', target_id='sc-old-a', **common),
    ]
    summaries = []
    for order in permutations(actions):
        revision = plan_task_constraint_revision([], old, list(order))
        assert revision.soft_constraints == []
        assert len(revision.checklist) == 1
        assert revision.checklist[0]['params']['slot_business_ids'] == ['S2']
        summaries.append({key: sorted(value) for key, value in (revision.summary() or {}).items()})
    # Do not prescribe a category; require the same complete batch to use the same category.
    assert summaries[0] == summaries[1], summaries

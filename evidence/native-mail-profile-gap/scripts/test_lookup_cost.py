import json,os,time
from pathlib import Path
from uuid import uuid7
import pytest
from django.db import transaction
from due.django.models import EffectAttempt
from due.django.evidence.attempts import locked_effect_attempt

@pytest.mark.django_db(transaction=True)
def test_lookup_cost():
    attempt=EffectAttempt.objects.create(work_model="tests.cost",work_id=7,claim_token=uuid7(),desired_revision=1,receipt={"members":["a"],"result":[]})
    readers={"orm":lambda:EffectAttempt.objects.select_for_update().get(pk=attempt.pk),"fixed":lambda:locked_effect_attempt(attempt.pk)}
    rows=[]
    for block in range(10):
        for arm in (["orm","fixed"] if block%2==0 else ["fixed","orm"]):
            with transaction.atomic():
                for _ in range(20):readers[arm]()
                cpu=time.process_time(); wall=time.perf_counter()
                for _ in range(1000):
                    value=readers[arm]()
                    assert value.receipt==attempt.receipt
                rows.append({"block":block,"arm":arm,"reads":1000,"wall_ms":(time.perf_counter()-wall)*1000,"cpu_ms":(time.process_time()-cpu)*1000})
    Path(os.environ["LOOKUP_COST_OUTPUT"]).write_text(json.dumps(rows,indent=2))

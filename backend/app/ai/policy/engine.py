"""Applies the central field policy to one record a read service returned.

`project` walks a record (and its nested records) and splits it into:

- `model`: only model-visible fields (MODEL, and MODEL_IF_VOCABULARY after the vocabulary gate);
- `deterministic`: model-visible, DETERMINISTIC_ONLY and PENDING_REVIEW fields, for
  server-written text and computed figures that no model reads;
- `withheld`: narrative strings from READER_ONLY, RESTRICTED and PENDING_REVIEW fields, which
  must never appear in a model request (checked before every provider call);
- `identifiers`: identifying values from RESTRICTED fields (checked in requests and output);
- `decisions`: the disposition of every field path seen, for diagnostics and tests.

An unclassified field anywhere raises `UntrustedContentError`: a new field cannot reach a model
until someone classifies it here.
"""

from dataclasses import dataclass, field

from app.ai.context_policy import UntrustedContentError, _strings
from app.ai.policy import approvals
from app.ai.policy.fields import FIELD_POLICY, IDENTIFYING, MODEL_VISIBLE, Disposition
from app.clinical import vocabulary

UNLISTED_MEDICATION = "an unlisted medication"


def is_vocabulary_drug(name: str | None) -> bool:
    """True only for a generic name in the approved list. A typed name never qualifies."""
    return bool(name) and vocabulary.drug_info(str(name))[0] is not None


@dataclass
class Projection:
    model: dict = field(default_factory=dict)
    deterministic: dict = field(default_factory=dict)
    withheld: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    decisions: dict[str, str] = field(default_factory=dict)


def _identifying_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _identifying_values(v)]
    if isinstance(value, list):
        return [s for v in value for s in _identifying_values(v)]
    return []


def _all_strings(value) -> list[str]:
    """Every string, however short: a typed medicine name is one word."""
    return _identifying_values(value)


def _walk(record: str, row: dict, out: Projection, path: str) -> tuple[dict, dict]:
    fields = FIELD_POLICY.get(record)
    if fields is None:
        raise UntrustedContentError(f"record type {record!r} has no field policy")
    if not isinstance(row, dict):
        raise UntrustedContentError(f"{path or record}: expected a record")
    unknown = set(row) - set(fields)
    if unknown:
        raise UntrustedContentError(f"{record} fields not classified: {sorted(unknown)}")
    model: dict = {}
    det: dict = {}
    for name, value in row.items():
        rule = fields[name]
        where = f"{path}{name}"
        if rule.startswith("@"):
            nested = rule[1:]
            out.decisions[where] = rule
            if value is None:
                model[name] = det[name] = None
            elif isinstance(value, list):
                pairs = [_walk(nested, v, out, f"{where}[].") for v in value]
                model[name] = [m for m, _ in pairs]
                det[name] = [d for _, d in pairs]
            else:
                model[name], det[name] = _walk(nested, value, out, f"{where}.")
            continue
        disposition = Disposition(rule)
        out.decisions[where] = disposition.value
        if disposition is Disposition.MODEL_IF_VOCABULARY:
            if value is None or is_vocabulary_drug(value):
                model[name] = det[name] = value
            else:
                model[name] = det[name] = UNLISTED_MEDICATION
                out.withheld += _all_strings(value)
                out.decisions[where] = "model_if_vocabulary:replaced"
        elif disposition in MODEL_VISIBLE:
            model[name] = det[name] = value
        elif disposition is Disposition.DETERMINISTIC_ONLY:
            det[name] = value
        elif disposition is Disposition.PENDING_REVIEW and approvals.field_approved(record, name):
            # Its data group was approved for model use (`approvals.FIELD_GROUPS`).
            model[name] = det[name] = value
            out.decisions[where] = "pending_review:approved"
        elif disposition is Disposition.PENDING_REVIEW:
            # Pending a data-use decision: server-side computation only, never a model, and
            # its text is withheld from every model request.
            det[name] = value
            out.withheld += _strings(value)
        else:  # READER_ONLY, RESTRICTED
            out.withheld += _strings(value)
            if disposition is Disposition.RESTRICTED and name in IDENTIFYING:
                out.identifiers += [v for v in _identifying_values(value) if v]
    return model, det


def project(record: str, row: dict) -> Projection:
    out = Projection()
    out.model, out.deterministic = _walk(record, row, out, "")
    return out


def disposition_of(record: str, name: str) -> str | None:
    """The rule for one field (`@record` for a nested one), or None when unclassified."""
    return FIELD_POLICY.get(record, {}).get(name)


__all__ = ["Projection", "project", "is_vocabulary_drug", "UNLISTED_MEDICATION"]

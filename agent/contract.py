"""The shapes of the three set-up answers: contract, handbook selection, plan.

Purpose: Pydantic models for what the model must produce before the work starts.
Why this design: free text cannot be checked by code; these shapes can. In particular
`ambiguities` drives a code rule: while it is non-empty the loop allows nothing but
asking the requester.
How it can fail: a model can list no ambiguities when there are some (it is then caught
later, when it hits a conflict while working) or list needless ones (it then asks a
question it did not need to). The prompt tells it to list only what looking cannot answer.
"""
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskContract(_Strict):
    goal: str = Field(description="One sentence: what a correct outcome is")
    entities: list[str] = Field(default_factory=list, description="Named things involved")
    ambiguities: list[str] = Field(default_factory=list, description=(
        "Questions that ONLY the requester can answer. Normally EMPTY. Never list: something the request already "
        "says (a name it mentions), which item is newest or largest, a value printed in a document, or whether "
        "something exists. Those are found by looking; put them under inputs_needed."))
    inputs_needed: list[str] = Field(default_factory=list, description="What must be found out or read")
    risk_notes: list[str] = Field(default_factory=list, description="Ways it could go wrong, limiting rules")

    def render(self) -> str:
        lines = [f"Goal: {self.goal}"]
        for label, items in (("Entities", self.entities), ("Inputs needed", self.inputs_needed), ("Risks", self.risk_notes)):
            if items:
                lines.append(f"{label}: " + "; ".join(items))
        if self.ambiguities:
            lines.append("Open questions for the requester: " + "; ".join(self.ambiguities))
        return "\n".join(lines)


class SopSelection(_Strict):
    sop_ids: list[str] = Field(description="Ids of the company rules whose work the request asks for (usually exactly one; may be empty)")
    reason: str = Field(description="One sentence")


class PlanSubmission(_Strict):
    items: list[str] = Field(min_length=1, max_length=8, description="Checklist items, in order")

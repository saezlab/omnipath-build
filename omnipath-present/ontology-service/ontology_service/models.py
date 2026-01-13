"""Pydantic models for API request/response schemas."""

from __future__ import annotations
from pydantic import BaseModel


class TermInfo(BaseModel):
    """Basic term information."""
    id: str
    name: str | None = None
    definition: str | None = None
    namespace: str | None = None


class TermWithRelations(TermInfo):
    """Term with parent/child relationships."""
    parents: list[str] = []
    children: list[str] = []


class TermsRequest(BaseModel):
    """Request for batch term lookup."""
    term_ids: list[str]


class TermsResponse(BaseModel):
    """Response for batch term lookup."""
    terms: dict[str, TermInfo | None]


class TrajectoryNode(BaseModel):
    """Node in a trajectory path."""
    id: str
    name: str | None = None
    distance: int = 0


class TreeNode(BaseModel):
    """Node in a hierarchy tree with children (recursive)."""
    id: str
    name: str | None = None
    distance: int = 0
    children: list[TreeNode] = []


class TrajectoryResponse(BaseModel):
    """Response for single term trajectory (all paths from root)."""
    term_id: str
    trajectories: list[list[TrajectoryNode]]


class TreeResponse(BaseModel):
    """Response with merged tree structure for multiple terms."""
    root: TreeNode | None = None


class OntologyInfo(BaseModel):
    """Information about an available ontology."""
    id: str
    description: str
    loaded: bool


class OntologiesResponse(BaseModel):
    """Response listing available ontologies."""
    ontologies: list[OntologyInfo]


class ErrorResponse(BaseModel):
    """Error response."""
    detail: str

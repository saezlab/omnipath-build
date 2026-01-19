"""FastAPI application for ontology service."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from ontograph.queries.introspection import IntrospectionPronto

from .models import (
    TermInfo,
    TermsRequest,
    TermsResponse,
    TrajectoryNode,
    TrajectoryResponse,
    TreeNode,
    TreeResponse,
    OntologyInfo,
    OntologiesResponse,
)
from .registry import registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload core ontologies on startup."""
    logger.info("Starting ontology service - preloading core ontologies...")
    registry.preload_core_ontologies()
    logger.info("Core ontologies loaded, service ready")
    yield
    logger.info("Shutting down ontology service")


app = FastAPI(
    title="Ontology Service",
    description="REST API for querying biological ontologies",
    version="0.1.0",
    lifespan=lifespan,
)


def get_ontology_or_404(ontology_id: str):
    """Get ontology client or raise 404."""
    client = registry.get(ontology_id)
    if client is None:
        raise HTTPException(
            status_code=404,
            detail=f"Ontology '{ontology_id}' not found or failed to load"
        )
    return client


def extract_term_info(client, term_id: str) -> TermInfo | None:
    """Extract term info from ontology client."""
    try:
        term = client.get_term(term_id)
        if term is None:
            return None
        return TermInfo(
            id=term.id,
            name=term.name,
            definition=str(term.definition) if term.definition else None,
            namespace=term.namespace,
        )
    except Exception:
        return None


def ontograph_node_to_tree_node(node) -> TreeNode:
    """Convert ontograph's internal Node to our TreeNode model."""
    return TreeNode(
        id=node.id,
        name=node.name,
        distance=node.distance,
        children=[ontograph_node_to_tree_node(c) for c in node.children.values()]
    )


# --- Health ---

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# --- Ontology listing ---

@app.get("/ontologies", response_model=OntologiesResponse)
async def list_ontologies():
    """List all available ontologies."""
    ontologies = [
        OntologyInfo(
            id=ont_id,
            description=desc,
            loaded=registry.is_loaded(ont_id)
        )
        for ont_id, desc in registry.list_available().items()
    ]
    return OntologiesResponse(ontologies=ontologies)


# --- Term lookup ---

@app.get("/{ontology_id}/term/{term_id}", response_model=TermInfo)
async def get_term(ontology_id: str, term_id: str):
    """Get term information by ID."""
    client = get_ontology_or_404(ontology_id)
    term_info = extract_term_info(client, term_id)
    if term_info is None:
        raise HTTPException(status_code=404, detail=f"Term '{term_id}' not found")
    return term_info


@app.post("/terms", response_model=TermsResponse)
async def get_terms_batch(request: TermsRequest):
    """Batch lookup of terms across multiple ontologies.
    
    Auto-detects ontology from term prefix (GO:, MI:, KW:, etc.)
    """
    from .config import get_ontology_for_term
    
    terms: dict[str, TermInfo | None] = {}
    
    # Group terms by ontology
    terms_by_ontology: dict[str, list[str]] = {}
    for term_id in request.term_ids:
        ontology_id = get_ontology_for_term(term_id)
        if ontology_id:
            terms_by_ontology.setdefault(ontology_id, []).append(term_id)
        else:
            # No matching ontology, term will be None
            terms[term_id] = None
    
    # Look up terms in each ontology
    for ontology_id, term_ids in terms_by_ontology.items():
        client = registry.get(ontology_id)
        if client is None:
            for term_id in term_ids:
                terms[term_id] = None
            continue
        for term_id in term_ids:
            terms[term_id] = extract_term_info(client, term_id)
    
    return TermsResponse(terms=terms)


# --- Navigation ---

@app.get("/{ontology_id}/term/{term_id}/parents")
async def get_parents(ontology_id: str, term_id: str):
    """Get direct parents of a term."""
    client = get_ontology_or_404(ontology_id)
    try:
        parents = client.get_parents(term_id)
        return {"term_id": term_id, "parents": [str(p) for p in parents]}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/{ontology_id}/term/{term_id}/ancestors")
async def get_ancestors(
    ontology_id: str,
    term_id: str,
    depth: int | None = Query(None, description="Maximum depth to traverse")
):
    """Get all ancestors of a term."""
    client = get_ontology_or_404(ontology_id)
    try:
        ancestors = client.get_ancestors(term_id, distance=depth)
        return {"term_id": term_id, "ancestors": [str(a) for a in ancestors]}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/{ontology_id}/term/{term_id}/children")
async def get_children(ontology_id: str, term_id: str):
    """Get direct children of a term."""
    client = get_ontology_or_404(ontology_id)
    try:
        children = client.get_children(term_id)
        return {"term_id": term_id, "children": [str(c) for c in children]}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/{ontology_id}/term/{term_id}/descendants")
async def get_descendants(
    ontology_id: str,
    term_id: str,
    depth: int | None = Query(None, description="Maximum depth to traverse")
):
    """Get all descendants of a term."""
    client = get_ontology_or_404(ontology_id)
    try:
        descendants = client.get_descendants(term_id, distance=depth)
        return {"term_id": term_id, "descendants": [str(d) for d in descendants]}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Trajectory / Hierarchy ---

@app.get("/{ontology_id}/term/{term_id}/trajectories", response_model=TrajectoryResponse)
async def get_trajectories(ontology_id: str, term_id: str):
    """Get all trajectories (paths) from root to a term.
    
    Uses ontograph's get_trajectories_from_root which returns all
    paths from root to the term (multiple paths if term has multiple parents).
    """
    client = get_ontology_or_404(ontology_id)
    try:
        trajectories = client.get_trajectories_from_root(term_id)
        # Convert to response format
        result = []
        for traj in trajectories:
            nodes = [
                TrajectoryNode(
                    id=node['id'],
                    name=node.get('name'),
                    distance=node.get('distance', 0)
                )
                for node in traj
            ]
            result.append(nodes)
        return TrajectoryResponse(term_id=term_id, trajectories=result)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/tree", response_model=TreeResponse)
async def get_tree(request: TermsRequest):
    """Get merged tree for terms across multiple ontologies.
    
    Auto-detects ontology from term prefix (GO:, MI:, KW:, etc.)
    Collects all trajectories for the given terms and merges them
    into a single tree structure with shared ancestor nodes combined.
    """
    from .config import get_ontology_for_term
    
    # Group terms by ontology
    terms_by_ontology: dict[str, list[str]] = {}
    for term_id in request.term_ids:
        ontology_id = get_ontology_for_term(term_id)
        if ontology_id:
            terms_by_ontology.setdefault(ontology_id, []).append(term_id)
    
    # Collect trajectories from all ontologies
    all_trajectories = []
    for ontology_id, term_ids in terms_by_ontology.items():
        client = registry.get(ontology_id)
        if client is None:
            continue
        for term_id in term_ids:
            try:
                trajectories = client.get_trajectories_from_root(term_id)
                all_trajectories.extend(trajectories)
            except Exception:
                continue
    
    if not all_trajectories:
        return TreeResponse(root=None)
    
    # Build merged tree using ontograph's implementation
    ontograph_root = IntrospectionPronto._build_tree_from_trajectories(all_trajectories)
    root = ontograph_node_to_tree_node(ontograph_root)
    return TreeResponse(root=root)


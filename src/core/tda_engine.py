"""
core/tda_engine.py
==================
Core TDA engine: dynamic filtered simplicial complex construction,
incremental persistent homology (H0, H1, H2), and multi-scale witness complex.
"""

from __future__ import annotations
import numpy as np
import time
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict
import heapq

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Simplex:
    """A simplex (vertex, edge, triangle, ...) in the filtered complex."""
    vertices: Tuple[int, ...]
    filtration_value: float
    dimension: int = field(init=False)

    def __post_init__(self):
        self.vertices = tuple(sorted(self.vertices))
        self.dimension = len(self.vertices) - 1

    def __hash__(self):
        return hash(self.vertices)

    def __eq__(self, other):
        return self.vertices == other.vertices

    def __lt__(self, other):
        # Lexicographic: filtration value first, then dimension
        return (self.filtration_value, self.dimension) < (other.filtration_value, other.dimension)


@dataclass
class PersistencePair:
    """A birth-death pair from persistent homology."""
    dimension: int
    birth: float
    death: float       # np.inf means the feature is still alive

    @property
    def persistence(self) -> float:
        return self.death - self.birth if not np.isinf(self.death) else np.inf

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension,
            "birth": self.birth,
            "death": self.death if not np.isinf(self.death) else None,
            "persistence": self.persistence if not np.isinf(self.persistence) else None,
        }


@dataclass
class PersistenceDiagram:
    """Collection of persistence pairs for a given time step."""
    timestamp: float
    pairs: List[PersistencePair] = field(default_factory=list)
    betti_numbers: Dict[int, int] = field(default_factory=dict)

    def pairs_by_dim(self, dim: int) -> List[PersistencePair]:
        return [p for p in self.pairs if p.dimension == dim]

    def as_point_cloud(self, dim: int) -> np.ndarray:
        """Return (birth, death) array for dimension dim (finite pairs only)."""
        pts = [(p.birth, p.death) for p in self.pairs_by_dim(dim)
               if not np.isinf(p.death)]
        return np.array(pts) if pts else np.zeros((0, 2))


# ---------------------------------------------------------------------------
# Incremental boundary matrix reduction (Simon's algorithm, Z/2 coefficients)
# ---------------------------------------------------------------------------

class BoundaryMatrixReducer:
    """
    Implements the standard persistence algorithm over Z/2 using
    a column-sparse representation with a low (pivot) index lookup.
    Supports online insertion of new simplices (Elder rule).
    """

    def __init__(self):
        # columns[sigma_idx] = set of row indices (Z/2 boundary chain)
        self.columns: List[set] = []
        # pivot_to_col[row] = col_index owning that pivot
        self.pivot_to_col: Dict[int, int] = {}
        self.simplex_list: List[Simplex] = []

    def add_simplex(self, simplex: Simplex, boundary_indices: List[int]) -> Optional[Tuple[int, int]]:
        """
        Add a simplex and its boundary (as indices into simplex_list).
        Returns (killer_idx, created_idx) if a pair closes a feature,
        or None if the simplex creates a new feature.
        """
        col_idx = len(self.columns)
        self.simplex_list.append(simplex)
        col = set(boundary_indices)
        self.columns.append(col)

        # Standard persistence algorithm (Edelsbrunner et al. 2002):
        # Reduce column by XOR-ing with existing pivot columns.
        # When column is non-zero with pivot p:
        #   - If pivot_to_col[p] exists → XOR to reduce further
        #   - If not → this simplex KILLS the feature born at simplex p
        #              record pair (current_simplex, simplex_p)
        while col:
            pivot = max(col)
            if pivot not in self.pivot_to_col:
                # This simplex kills the feature born at index `pivot`
                self.pivot_to_col[pivot] = col_idx
                return (col_idx, pivot)   # (killer, born)
            else:
                # XOR with existing column to reduce
                reducing_col = self.columns[self.pivot_to_col[pivot]]
                col = col.symmetric_difference(reducing_col)
                self.columns[col_idx] = col

        # Column reduced to zero → this simplex creates an essential class
        # (never paired = infinite bar)
        return None  # Essential feature (infinite lifetime)


# ---------------------------------------------------------------------------
# Dynamic filtered simplicial complex
# ---------------------------------------------------------------------------

class DynamicFilteredComplex:
    """
    Maintains a time-varying Vietoris-Rips-like simplicial complex over
    a supply-chain graph. Nodes = facilities, edges = cargo flow weight.

    For efficiency we use a 2-skeleton (up to H2).
    Multi-scale witness complex option for sparse data.
    """

    MAX_DIM = 2   # Compute H0, H1, H2
    MAX_SIMPLICES = 2000  # Cap total simplex count for performance

    def __init__(self, max_edge_weight: float = 1.0, witness: bool = False,
                 n_landmarks: int = 50):
        self.max_edge_weight = max_edge_weight
        self.use_witness = witness
        self.n_landmarks = n_landmarks

        # Indexed by vertex tuple → Simplex
        self.simplices: Dict[Tuple, Simplex] = {}
        # Filtration order (sorted by filtration value)
        self._dirty = False
        self._sorted_simplices: List[Simplex] = []

        # Graph adjacency for triangle enumeration
        self.adj: Dict[int, Dict[int, float]] = defaultdict(dict)
        self.node_positions: Dict[int, np.ndarray] = {}   # for witness complex

    def update_node(self, node_id: int, position: Optional[np.ndarray] = None,
                    weight: float = 0.0):
        """Upsert a node (facility) with optional spatial position.
        
        Nodes get a tiny unique epsilon so that H0 reduction pairs them
        correctly (avoids ties in filtration order).
        """
        key = (node_id,)
        # Small epsilon ensures distinct filtration values for each node
        # so the persistence algorithm can pair them properly
        eps = node_id * 1e-9
        s = Simplex(key, filtration_value=weight + eps)
        self.simplices[key] = s
        if position is not None:
            self.node_positions[node_id] = position
        self._dirty = True

    def update_edge(self, u: int, v: int, weight: float):
        """
        Upsert an edge representing cargo flow.
        weight encodes flow delay/congestion (higher = more anomalous).
        """
        key = tuple(sorted((u, v)))
        s = Simplex(key, filtration_value=weight)
        self.simplices[key] = s
        self.adj[u][v] = weight
        self.adj[v][u] = weight

        # Auto-close triangles (H2 computation)
        if self.MAX_DIM >= 2:
            common = set(self.adj.get(u, {})) & set(self.adj.get(v, {}))
            for w in common:
                if w != u and w != v:
                    tri_key = tuple(sorted((u, v, w)))
                    tri_weight = max(weight,
                                     self.adj[u].get(w, np.inf),
                                     self.adj[v].get(w, np.inf))
                    if tri_weight <= self.max_edge_weight * 1.5:
                        self.simplices[tri_key] = Simplex(tri_key, tri_weight)
        self._dirty = True

    def remove_edge(self, u: int, v: int):
        """Remove an edge (e.g., route failure)."""
        key = tuple(sorted((u, v)))
        self.simplices.pop(key, None)
        self.adj[u].pop(v, None)
        self.adj[v].pop(u, None)
        self._dirty = True

    def _witness_complex_landmarks(self) -> List[int]:
        """Select landmark nodes via maxmin for witness complex."""
        if not self.node_positions:
            nodes = list(self.adj.keys())
            step = max(1, len(nodes) // self.n_landmarks)
            return nodes[::step][:self.n_landmarks]

        positions = np.array(list(self.node_positions.values()))
        node_ids = list(self.node_positions.keys())
        n = len(node_ids)
        if n <= self.n_landmarks:
            return node_ids

        # Greedy maxmin
        selected = [0]
        dists = np.full(n, np.inf)
        for _ in range(self.n_landmarks - 1):
            d = np.linalg.norm(positions - positions[selected[-1]], axis=1)
            dists = np.minimum(dists, d)
            selected.append(int(np.argmax(dists)))
        return [node_ids[i] for i in selected]

    def get_sorted_simplices(self) -> List[Simplex]:
        """Return simplices sorted by filtration value (dimension as tiebreaker)."""
        if self._dirty:
            self._sorted_simplices = sorted(self.simplices.values())
            self._dirty = False
        return self._sorted_simplices

    @property
    def n_simplices(self) -> int:
        return len(self.simplices)

    @property
    def n_nodes(self) -> int:
        return sum(1 for k in self.simplices if len(k) == 1)

    @property
    def n_edges(self) -> int:
        return sum(1 for k in self.simplices if len(k) == 2)


# ---------------------------------------------------------------------------
# Persistent homology computer (online, incremental)
# ---------------------------------------------------------------------------

class PersistentHomologyComputer:
    """
    Computes H0, H1, H2 persistent homology from a DynamicFilteredComplex.
    Uses an incremental variant: only re-reduces newly added simplices.
    """

    def __init__(self):
        self.reducer = BoundaryMatrixReducer()
        self._simplex_index: Dict[Tuple, int] = {}
        self._pairs: List[Tuple[int, int]] = []   # (killer_idx, born_idx)
        self._essential: List[int] = []            # column indices with no death

    def _boundary_indices(self, simplex: Simplex) -> List[int]:
        """Return column indices of the facets of simplex."""
        if simplex.dimension == 0:
            return []
        indices = []
        verts = list(simplex.vertices)
        for i in range(len(verts)):
            face = tuple(verts[:i] + verts[i+1:])
            if face in self._simplex_index:
                indices.append(self._simplex_index[face])
        return sorted(indices)

    def update(self, complex: DynamicFilteredComplex) -> PersistenceDiagram:
        """
        (Re-)compute persistence from the current complex state.
        Returns a PersistenceDiagram at the current timestamp.
        """
        t0 = time.time()
        # Full recomputation (for correctness; incremental opt possible)
        self.reducer = BoundaryMatrixReducer()
        self._simplex_index = {}
        self._pairs = []
        self._essential = []

        pairs: List[PersistencePair] = []
        simplex_list = complex.get_sorted_simplices()

        all_pairs_raw = []  # (killer_idx, born_idx)

        for idx, simplex in enumerate(simplex_list):
            self._simplex_index[simplex.vertices] = idx
            boundary = self._boundary_indices(simplex)
            result = self.reducer.add_simplex(simplex, boundary)
            if result is not None:
                killer_idx, born_idx = result
                all_pairs_raw.append((killer_idx, born_idx))
                killer = simplex_list[killer_idx]
                born = simplex_list[born_idx]
                # The feature was born at `born` and killed at `killer`
                if killer.filtration_value > born.filtration_value:
                    pairs.append(PersistencePair(
                        dimension=born.dimension,
                        birth=born.filtration_value,
                        death=killer.filtration_value,
                    ))

        # Essential = simplices NOT killed by any other simplex
        killed_indices = {born_idx for _, born_idx in all_pairs_raw}
        killer_indices = {killer_idx for killer_idx, _ in all_pairs_raw}
        for idx, simplex in enumerate(simplex_list):
            if idx not in killed_indices and idx not in killer_indices:
                self._essential.append(idx)

        # Essential classes (infinite bars)
        for idx in self._essential:
            s = simplex_list[idx]
            pairs.append(PersistencePair(
                dimension=s.dimension,
                birth=s.filtration_value,
                death=np.inf,
            ))

        elapsed = time.time() - t0
        logger.debug(f"Persistent homology computed in {elapsed:.3f}s, "
                     f"{len(pairs)} pairs from {len(simplex_list)} simplices")

        # Betti numbers = count of infinite pairs per dimension
        betti = defaultdict(int)
        for p in pairs:
            if np.isinf(p.death):
                betti[p.dimension] += 1

        return PersistenceDiagram(
            timestamp=time.time(),
            pairs=pairs,
            betti_numbers=dict(betti),
        )


# ---------------------------------------------------------------------------
# Wasserstein distance between persistence diagrams
# ---------------------------------------------------------------------------

def wasserstein_distance(diag_a: PersistenceDiagram,
                         diag_b: PersistenceDiagram,
                         dim: int = 1,
                         order: int = 2,
                         max_points: int = 50) -> float:
    """
    Compute the p-Wasserstein distance between two persistence diagrams
    for a given homological dimension, using a greedy optimal transport
    approximation (Hungarian for small sizes).

    Points at infinity are handled by projecting to the diagonal.
    max_points: cap on diagram size for performance (keep highest-persistence points).
    """
    pts_a = diag_a.as_point_cloud(dim)
    pts_b = diag_b.as_point_cloud(dim)

    # Truncate to highest-persistence points for performance
    def top_k(pts, k):
        if len(pts) <= k:
            return pts
        persist = pts[:, 1] - pts[:, 0]
        idx = np.argsort(persist)[-k:]
        return pts[idx]

    pts_a = top_k(pts_a, max_points)
    pts_b = top_k(pts_b, max_points)

    if pts_a.shape[0] == 0 and pts_b.shape[0] == 0:
        return 0.0

    def diagonal_point(pt):
        mid = (pt[0] + pt[1]) / 2.0
        return np.array([mid, mid])

    # Augment with diagonal projections to handle unequal sizes
    all_a = list(pts_a) + [diagonal_point(p) for p in pts_b]
    all_b = list(pts_b) + [diagonal_point(p) for p in pts_a]

    n = len(all_a)
    cost_matrix = np.zeros((n, n))
    for i, a in enumerate(all_a):
        for j, b in enumerate(all_b):
            cost_matrix[i, j] = np.linalg.norm(a - b) ** order

    # Hungarian algorithm
    try:
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        total = cost_matrix[row_ind, col_ind].sum()
    except ImportError:
        # Greedy fallback
        total = 0.0
        used_cols = set()
        for i in range(n):
            best_j = min((j for j in range(n) if j not in used_cols),
                         key=lambda j: cost_matrix[i, j])
            total += cost_matrix[i, best_j]
            used_cols.add(best_j)

    return float(total ** (1.0 / order))


def multi_dim_wasserstein(diag_a: PersistenceDiagram,
                          diag_b: PersistenceDiagram,
                          dims: Tuple[int, ...] = (0, 1, 2),
                          weights: Tuple[float, ...] = (0.2, 0.5, 0.3)) -> float:
    """Weighted sum of Wasserstein distances across dimensions."""
    total = 0.0
    for dim, w in zip(dims, weights):
        total += w * wasserstein_distance(diag_a, diag_b, dim=dim)
    return total


# ---------------------------------------------------------------------------
# Persistence landscape
# ---------------------------------------------------------------------------

def persistence_landscape(diagram: PersistenceDiagram,
                           dim: int = 1,
                           n_layers: int = 5,
                           resolution: int = 100) -> np.ndarray:
    """
    Compute the persistence landscape λ_k(t) for the given dimension.
    Returns array of shape (n_layers, resolution).
    """
    pts = diagram.as_point_cloud(dim)
    if pts.shape[0] == 0:
        return np.zeros((n_layers, resolution))

    b_min = pts[:, 0].min()
    d_max = pts[:, 1].max()
    t_grid = np.linspace(b_min, d_max, resolution)

    # For each t, compute tent function value for each pair
    tents = np.zeros((pts.shape[0], resolution))
    for i, (b, d) in enumerate(pts):
        mid = (b + d) / 2.0
        for j, t in enumerate(t_grid):
            if b <= t <= mid:
                tents[i, j] = t - b
            elif mid < t <= d:
                tents[i, j] = d - t

    # λ_k is the k-th largest tent at each t
    tents_sorted = np.sort(tents, axis=0)[::-1]
    return tents_sorted[:n_layers]

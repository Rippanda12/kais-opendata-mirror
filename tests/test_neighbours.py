from shapely import STRtree
from shapely.geometry import Polygon

from kaismirror.neighbours import TOLERANCE_M


def near(geoms):
    tree = STRtree(geoms)
    return [sorted(int(j) for j in tree.query(g.buffer(TOLERANCE_M), predicate="intersects") if j != i)
            for i, g in enumerate(geoms)]


def test_touching_outlines_are_neighbours():
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(10.02, 0), (20, 0), (20, 10), (10.02, 10)])  # 2 cm drawing gap still counts
    far = Polygon([(100, 100), (110, 100), (110, 110), (100, 110)])
    corner = Polygon([(10, 10), (12, 10), (12, 12), (10, 12)])  # touches a and b at a corner
    assert near([a, b, far, corner]) == [[1, 3], [0, 3], [], [0, 1]]

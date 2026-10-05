"""Small dependency-free world-pose operations; quaternion order is xyzw."""
import math


def add(a, b):
    return [x+y for x, y in zip(a, b)]


def sub(a, b):
    return [x-y for x, y in zip(a, b)]


def norm(a):
    return math.sqrt(sum(x*x for x in a))


def mul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z]


def inverse(q):
    return [-q[0], -q[1], -q[2], q[3]]


def rotate(q, p):
    return mul(mul(q, [*p, 0.]), inverse(q))[:3]


def axis_angle(axis, angle):
    scale = math.sin(angle/2)/norm(axis)
    return [*(x*scale for x in axis), math.cos(angle/2)]


def compose(a, b):
    return (add(a[0], rotate(a[1], b[0])), mul(a[1], b[1]))


def relative(parent, child):
    q = inverse(parent[1])
    return (rotate(q, sub(child[0], parent[0])), mul(q, child[1]))


def pose(record):
    return (list(record['position_m']), list(record['rotation_xyzw']))


def angle(a, b):
    dot = min(1., abs(sum(x*y for x, y in zip(a, b))))
    return 2*math.acos(dot)


def smooth(u):
    u = min(1., max(0., u))
    return u*u*(3-2*u)


def lerp(a, b, u):
    return [x+(y-x)*u for x, y in zip(a, b)]


def interpolate_knots(knots, u):
    if u <= knots[0][0]:
        return list(knots[0][1])
    for (start, a), (end, b) in zip(knots, knots[1:]):
        if u <= end:
            return lerp(a, b, smooth((u-start)/(end-start)))
    return list(knots[-1][1])

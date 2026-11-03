"""学習者モデルの式。analyze.py（分野ごと）と route.py（論点ごと）が共有する。標準ライブラリのみ。

力 θ と不確かさ v を持ち、1回の結果 y（0〜1）ごとに Laplace 近似の Elo で更新する
（imohiyoko/learning の ADR 0018 の 5.1 と同じ式）。見込みは p = σ(θ - b)、b は問題の難しさ。
"""
import math

V_INIT = 1.0     # 観測前の不確かさ
V_MIN = 0.05     # 不確かさの下限（古い結果に縛られすぎないように）


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def logit(p):
    return math.log(p / (1 - p))


def elo_update(m, y, w, b=0.0):
    """m の theta と v を、結果 y（重み w、難しさ b）で更新する。"""
    p = sigmoid(m["theta"] - b)
    info = w * m["v"] * p * (1 - p)
    m["theta"] += w * m["v"] * (y - p) / (1 + info)
    m["v"] = max(m["v"] / (1 + info), V_MIN)


def interval(theta, v, z=1.96):
    """見込み p と、その 95% の幅 (low, high)。"""
    sd = math.sqrt(v)
    return sigmoid(theta), sigmoid(theta - z * sd), sigmoid(theta + z * sd)

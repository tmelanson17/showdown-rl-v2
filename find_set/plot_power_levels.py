import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

df = pd.read_csv("find_set/power_levels.csv")

physical = (
    df[df["category"] == "Physical"].sort_values("avg_pct").reset_index(drop=True)
)
special = df[df["category"] == "Special"].sort_values("avg_pct").reset_index(drop=True)

# rank -> (short label, hex color)
physical_benchmarks = {
    1414: ("Garchomp Max Dragon Claw", "#1D0EE4"),
    1755: ("Sneasler Invested CC", "#E8731C"),
    1810: ("Sneasler Max CC", "#2196F3"),
    1898: ("Basculegion Adap. Inv.\n2KO Last Respects", "#9C27B0"),
    1776: ("Kangaskhan Max Last Resort", "#4CAF50"),
}
special_benchmarks = {
    1244: ("Sylveon Max Hyper Voice", "#E91E63"),
    1830: ("Archaludon Max Electro Shot", "#2196F3"),
    1770: ("Charizard-MegaY Inv. Heat Wave", "#FF9800"),
    1892: ("Charizard-MegaY Inv.\nWeather Ball", "#F44336"),
    1909: ("Floette-Mega Inv.\nLight of Ruin", "#9C27B0"),
}
aero_defense_benchmark = {
    31.3: ("Sash Aerodactyl 2HKO", "#E9A91E"),
    62.7: ("Sash Aerodactyl Bulk", "#E9A91E"),
    46.7: ("HP Invested Mega Aerodactyl 2HKO", "#1EE9CE"),
    93.5: ("HP Invested Mega Aerodactyl Bulk", "#1EE9CE"),
}


def spread_positions(values, min_gap=5.5):
    """Push overlapping label y-positions apart while preserving order."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    pos = [values[i] for i in order]
    for _ in range(300):
        moved = False
        for i in range(1, n):
            if pos[i] - pos[i - 1] < min_gap:
                mid = (pos[i] + pos[i - 1]) / 2
                pos[i - 1] = mid - min_gap / 2
                pos[i] = mid + min_gap / 2
                moved = True
        if not moved:
            break
    result = [0.0] * n
    for new_i, orig_i in enumerate(order):
        result[orig_i] = pos[new_i]
    return result


def find_boundary_idx(values, key):
    # Binary search for the largest value less than key
    upper_idx = len(values)
    lower_idx = 0
    while upper_idx > lower_idx:
        pivot = int((upper_idx + lower_idx) // 2)
        if values[pivot] >= key:
            upper_idx = pivot
        else:
            lower_idx = pivot + 1
    return max(lower_idx - 1, 0)


def make_chart(ax, data, benchmarks, title, defense_benchmarks=None):
    n = len(data)
    values = data["avg_pct"].values
    ranks = data["rank"].values

    rank_to_idx = {int(r): i for i, r in enumerate(ranks)}

    colors = ["#7DB3D8"] * n
    bench_info = []
    for rank, (label, color) in benchmarks.items():
        if rank in rank_to_idx:
            idx = rank_to_idx[rank]
            colors[idx] = color
            bench_info.append((idx, float(values[idx]), label, color))

    if defense_benchmarks is not None:
        for value, (label, color) in defense_benchmarks.items():
            idx = find_boundary_idx(values, value)
            colors[idx] = color
            bench_info.append((idx, value, label, color))

    ax.bar(np.arange(n), values, width=1.0, color=colors, linewidth=0, zorder=2)

    y_min = float(values.min())
    y_max = float(values.max())
    y_range = y_max - y_min

    # Min label
    row0 = data.iloc[0]
    ax.annotate(
        f"Min: {y_min:.1f}%\n{row0['pokemon']} {row0['move']}",
        xy=(0, y_min),
        xytext=(n * 0.04, y_min + y_range * 0.13),
        fontsize=7.5,
        fontweight="bold",
        color="#222222",
        ha="left",
        arrowprops=dict(arrowstyle="->", color="#444", lw=0.9),
        bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85, ec="#aaa", lw=0.5),
        zorder=5,
    )

    # Max label
    rowN = data.iloc[-1]
    ax.annotate(
        f"Max: {y_max:.1f}%\n{rowN['pokemon']} {rowN['move']}",
        xy=(n - 1, y_max),
        xytext=(n * 0.55, y_max - y_range * 0.09),
        fontsize=7.5,
        fontweight="bold",
        color="#222222",
        ha="left",
        arrowprops=dict(arrowstyle="->", color="#444", lw=0.9),
        bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85, ec="#aaa", lw=0.5),
        zorder=5,
    )

    # Benchmark labels on right side
    bench_info.sort(key=lambda x: x[1])
    raw_y = [v for _, v, _, _ in bench_info]
    label_y = spread_positions(raw_y, min_gap=6.0)
    label_x = n + n * 0.025

    for i, (idx, val, label, color) in enumerate(bench_info):
        ax.axhline(y=val, color=color, linestyle="--", lw=0.75, alpha=0.5, zorder=1)
        ax.annotate(
            f"{label}\n{val:.1f}%",
            xy=(n - 1, val),
            xytext=(label_x, label_y[i]),
            fontsize=6.8,
            color=color,
            fontweight="bold",
            ha="left",
            va="center",
            arrowprops=dict(arrowstyle="-", color=color, lw=0.7),
            zorder=5,
        )

    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_xlabel("Moves sorted by power level", fontsize=9)
    ax.set_ylabel(
        "Avg % Damage to Uninvested Mythical (100/100/100 defenses)", fontsize=9
    )
    ax.tick_params(axis="x", bottom=False, labelbottom=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.2, zorder=0)
    ax.set_xlim(-5, n + n * 0.60)


fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(22, 8))
fig.patch.set_facecolor("#FAFAFA")
fig.suptitle("Pokémon Move Power Levels", fontsize=15, fontweight="bold")

make_chart(
    ax1,
    physical,
    physical_benchmarks,
    "Physical Moves",
    defense_benchmarks=aero_defense_benchmark,
)
make_chart(ax2, special, special_benchmarks, "Special Moves")

plt.tight_layout(rect=[0, 0, 1, 0.96])
out = "find_set/power_levels_chart.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
plt.close()
print(f"Saved: {out}")
print(f"Physical entries: {len(physical)}  |  Special entries: {len(special)}")

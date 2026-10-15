import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
from pathlib import Path

DATA_DIR = Path("/app/bulgaria_rila_pirin/data")
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR = DATA_DIR.parent / "outputs" / "meteo_plots"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
TEMP_LINE = "#2a78d6"   # categorical slot 1 / sequential blue
TEMP_BAND = "#9ec5f4"   # sequential blue, lighter step

COVERAGE_COLORS = {
    "complete": "#0ca30c",
    "partial": "#fab219",
    "gap": "#d03b3b",
}
COVERAGE_ORDER = ["complete", "partial", "gap"]

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK_SECONDARY,
    "text.color": INK_PRIMARY,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "grid.color": GRIDLINE,
    "font.family": "sans-serif",
})

daily = pd.read_csv(DATA_DIR / "stations_daily_full_history.csv", parse_dates=["date"])
metadata = pd.read_csv(DATA_DIR / "selected_stations_metadata.csv").set_index("NodeID")


def plot_station(node, sub):
    name = sub["station_name"].iloc[0]
    altitude = metadata.loc[node, "Altitude"] if node in metadata.index else None
    sub = sub.sort_values("date")

    fig = plt.figure(figsize=(13, 6))
    gs = gridspec.GridSpec(2, 2, width_ratios=[4, 1], height_ratios=[5, 1],
                            hspace=0.08, wspace=0.28, figure=fig)
    ax_temp = fig.add_subplot(gs[0, 0])
    ax_strip = fig.add_subplot(gs[1, 0], sharex=ax_temp)
    ax_counts = fig.add_subplot(gs[:, 1])

    ax_temp.fill_between(sub["date"], sub["temp_c_min"], sub["temp_c_max"],
                          color=TEMP_BAND, alpha=0.6, linewidth=0, label="Min–max range")
    ax_temp.plot(sub["date"], sub["temp_c_mean"], color=TEMP_LINE, linewidth=1.2, label="Daily mean")
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.grid(axis="y", linewidth=0.6)
    ax_temp.set_axisbelow(True)
    for spine in ("top", "right"):
        ax_temp.spines[spine].set_visible(False)
    ax_temp.tick_params(labelbottom=False)
    ax_temp.legend(loc="upper left", frameon=False, labelcolor=INK_SECONDARY, fontsize=9)

    alt_str = f", {altitude:.0f} m" if altitude is not None else ""
    start, end = sub["date"].min(), sub["date"].max()
    ax_temp.set_title(
        f"{name} ({node}{alt_str}) — daily temperature, {start.date()} to {end.date()}",
        color=INK_PRIMARY, fontsize=13, loc="left")

    colors = sub["coverage"].map(COVERAGE_COLORS).fillna(INK_MUTED)
    ax_strip.bar(sub["date"], height=1, width=1.0, color=colors, linewidth=0, align="edge")
    ax_strip.set_ylim(0, 1)
    ax_strip.set_yticks([])
    ax_strip.set_ylabel("coverage", fontsize=8, rotation=0, ha="right", va="center")
    for spine in ax_strip.spines.values():
        spine.set_visible(False)
    ax_strip.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax_strip.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax_strip.xaxis.get_major_locator()))
    ax_strip.set_xlabel("Date")

    counts = sub["coverage"].value_counts().reindex(COVERAGE_ORDER, fill_value=0)
    bars = ax_counts.bar(COVERAGE_ORDER, counts.values,
                          color=[COVERAGE_COLORS[c] for c in COVERAGE_ORDER])
    ax_counts.set_title("Days by category", fontsize=11, color=INK_PRIMARY)
    ax_counts.set_ylabel("Number of days")
    ax_counts.grid(axis="y", linewidth=0.6)
    ax_counts.set_axisbelow(True)
    for spine in ("top", "right"):
        ax_counts.spines[spine].set_visible(False)
    total = counts.sum()
    for bar, val in zip(bars, counts.values):
        pct = 100 * val / total if total else 0
        ax_counts.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"{val}\n({pct:.0f}%)", ha="center", va="bottom", fontsize=8,
                        color=INK_SECONDARY)
    ax_counts.margins(y=0.15)

    safe_name = name.replace(" ", "_")
    out_path = OUT_DIR / f"{node}_{safe_name}.png"
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return out_path


written = []
for node, sub in daily.groupby("node"):
    written.append(plot_station(node, sub))

# N211 (Makedonia_Hut) never reported any data at all — not present in the
# daily file, so it needs its own placeholder rather than being silently dropped.
if "N211" in metadata.index:
    fig, ax = plt.subplots(figsize=(13, 3))
    ax.set_axis_off()
    row = metadata.loc["N211"]
    ax.text(0.5, 0.5, f"{row['Location']} (N211, {row['Altitude']:.0f} m)\nNo data ever recorded",
            ha="center", va="center", fontsize=13, color=INK_MUTED, style="italic")
    out_path = OUT_DIR / "N211_Makedonia_Hut.png"
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    written.append(out_path)

print(f"Wrote {len(written)} per-station plots to {OUT_DIR}")

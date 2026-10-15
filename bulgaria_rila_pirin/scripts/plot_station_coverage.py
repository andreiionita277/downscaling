import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
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

STATUS_COLORS = {
    "good": "#0ca30c",
    "partial": "#fab219",
    "mostly_gap": "#d03b3b",
    "no_data": "#898781",
}
STATUS_ORDER = ["good", "partial", "mostly_gap", "no_data"]

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

df = pd.read_csv(DATA_DIR / "station_coverage_summary.csv", parse_dates=["start", "end"])
df = df.sort_values("start")

metadata = pd.read_csv(DATA_DIR / "selected_stations_metadata.csv").set_index("NodeID")
df["label"] = df.apply(
    lambda r: f"{r.station_name} ({metadata.loc[r.node, 'Altitude']:.0f} m)" if r.node in metadata.index else r.station_name,
    axis=1,
)

# Exact count of hours with real readings (complete + partial, i.e. not "gap"),
# not just n_hours_total * pct — computed from the actual per-hour records.
hourly_coverage = pd.read_csv(DATA_DIR / "stations_hourly_full_history.csv", usecols=["node", "coverage"])
hours_with_data = hourly_coverage[hourly_coverage["coverage"] != "gap"].groupby("node").size()
df["hours_with_data"] = df["node"].map(hours_with_data).fillna(0).astype(int)
df["label_hours"] = df.apply(lambda r: f"{r.station_name} ({r.hours_with_data:,} h)", axis=1)

# --- Plot 1: coverage timeline (when was each station active) ---
fig, ax = plt.subplots(figsize=(10, 8))
for i, row in enumerate(df.itertuples()):
    if pd.isna(row.start):
        ax.text(df["start"].min(), i, "no data ever recorded", va="center",
                 color=INK_MUTED, fontsize=9, style="italic")
        continue
    ax.barh(i, (row.end - row.start).days, left=row.start,
             color=STATUS_COLORS.get(row.overall_status, INK_MUTED), height=0.6)

ax.set_yticks(range(len(df)))
ax.set_yticklabels(df["label"])
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
ax.grid(axis="x", linewidth=0.6)
ax.set_axisbelow(True)
for spine in ("top", "right", "left"):
    ax.spines[spine].set_visible(False)
ax.set_xlabel("Date")
ax.set_title("METER.AC station coverage — Rila-Pirin AOI", color=INK_PRIMARY, fontsize=13)
ax.invert_yaxis()

handles = [plt.Rectangle((0, 0), 1, 1, color=STATUS_COLORS[s]) for s in STATUS_ORDER]
ax.legend(handles, STATUS_ORDER, loc="upper left", bbox_to_anchor=(1.01, 1.0),
          frameon=False, labelcolor=INK_SECONDARY, borderaxespad=0.0)

fig.tight_layout()
fig.savefig(OUT_DIR / "station_coverage_timeline.png", dpi=200, bbox_inches="tight")
plt.close(fig)

# --- Plot 2: data quality breakdown, sorted by completeness ---
df2 = df.sort_values("pct_complete", ascending=True)
fig2, ax2 = plt.subplots(figsize=(10, 8))
ax2.barh(df2["label_hours"], df2["pct_complete"], color=STATUS_COLORS["good"], label="Complete")
ax2.barh(df2["label_hours"], df2["pct_partial"], left=df2["pct_complete"],
         color=STATUS_COLORS["partial"], label="Partial")
ax2.barh(df2["label_hours"], df2["pct_gap"],
         left=df2["pct_complete"] + df2["pct_partial"],
         color=STATUS_COLORS["mostly_gap"], label="Gap")
ax2.grid(axis="x", linewidth=0.6)
ax2.set_axisbelow(True)
for spine in ("top", "right", "left"):
    ax2.spines[spine].set_visible(False)
ax2.set_xlabel("% of hours in station's own reporting period")
ax2.set_title("Data completeness by station", color=INK_PRIMARY, fontsize=13)
ax2.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False,
           labelcolor=INK_SECONDARY, borderaxespad=0.0)
fig2.tight_layout()
fig2.savefig(OUT_DIR / "station_data_quality.png", dpi=200, bbox_inches="tight")
plt.close(fig2)

print(f"Wrote {OUT_DIR / 'station_coverage_timeline.png'}")
print(f"Wrote {OUT_DIR / 'station_data_quality.png'}")

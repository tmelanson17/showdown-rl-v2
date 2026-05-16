"""
Minimal HTTP server that generates an interactive Plotly chart and serves it to the browser.
Run with: python server.py
Then open: http://localhost:8080

Requires: pip install plotly numpy pandas
"""

import orjson
import urllib.parse
import numpy as np
import plotly.graph_objects as go
import pandas as pd

from http.server import BaseHTTPRequestHandler, HTTPServer

from doesitlive.adjust_damages import adjust_damage, Type

PORT = 8080
_DAMAGE_DATA: dict | None = None


def _get_damage_data() -> dict:
    global _DAMAGE_DATA
    if _DAMAGE_DATA is None:
        _DAMAGE_DATA = load_damages("find_set/power_levels.csv")
    return _DAMAGE_DATA


def twohitko(hp: int) -> int:
    return hp // 2 + 1


def load_damages(damage_file):
    df = pd.read_csv(damage_file)
    damages = df["avg_damage"].to_numpy()
    multipliers = df["multiplier"].to_numpy()
    desc = df["desc"].to_numpy(dtype=str)
    category = df["category"].to_numpy(dtype=str)
    physical_idx = category == "Physical"
    special_idx = category == "Special"

    return {
        "Physical": (
            damages[physical_idx],
            multipliers[physical_idx],
            desc[physical_idx],
        ),
        "Special": (damages[special_idx], multipliers[special_idx], desc[special_idx]),
    }


def get_adjusted_damage(damages, multipliers, desc, defense):
    adjusted_damage = adjust_damage(
        damages, multipliers, defense, Type.NORMAL, Type.NORMAL, None
    )
    adjusted_damage.sort()
    x = np.arange(len(adjusted_damage)).tolist()
    return {"x": x, "y": adjusted_damage.tolist(), "desc": desc.tolist()}


def generate_plot(hp: int = 175) -> str:
    """Return a self-contained Plotly chart as an HTML div string."""
    data = load_damages("find_set/power_levels.csv")
    fig = go.Figure()
    for category in ("Physical", "Special"):
        damage_adj = get_adjusted_damage(*data[category], defense=1.0)

        fig.add_trace(
            go.Scatter(
                x=damage_adj["x"],
                y=damage_adj["y"],
                mode="markers",
                name=f"{category} Damage",
                customdata=damage_adj["desc"],
                hovertemplate="%{customdata}<extra></extra>",
            )
        )
    fig.add_hline(
        y=hp, line_dash="dot", label=dict(text="OHKO", textposition="top left")
    )
    fig.add_hline(
        y=twohitko(hp),
        line_dash="dot",
        label=dict(text="2HKO", textposition="top left"),
    )
    fig.update_layout(
        title="Does It Live?",
        xaxis_title="x",
        yaxis_title="y",
        hovermode="closest",
    )

    # full_html=False returns just the <div> + inline <script>, no <html> wrapper
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def build_page() -> bytes:
    chart_div = generate_plot()
    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Does It Live</title>
  <style>
    body {{
      font-family: sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2rem;
      background: #f5f5f5;
    }}
    .chart-wrap {{
      width: 90vw;
      max-width: 900px;
      background: white;
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,.15);
      padding: 1rem;
    }}
    .slider-wrap {{
      width: 90vw;
      max-width: 900px;
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-top: 1rem;
    }}
    .slider-wrap input[type=range] {{ flex: 1; }}
    .slider-wrap label {{ min-width: 10ch; }}
    .slider-wrap span {{ min-width: 4ch; text-align: right; font-variant-numeric: tabular-nums; }}
  </style>
</head>
<body>
  <h1>Does It Live?</h1>
  <div class="chart-wrap">
    {chart_div}
  </div>
  <div class="slider-wrap">
    <label for="hp-slider">HP</label>
    <input id="hp-slider" type="range" min="1" max="400" step="1" value="300">
    <span id="hp-label">300</span>
  </div>
  <div class="slider-wrap">
    <label for="defense-slider">Defense</label>
    <input id="defense-slider" type="range" min="1" max="300" step="1" value="120">
    <span id="defense-label">120</span>
  </div>
  <div class="slider-wrap">
    <label for="spdef-slider">Sp. Defense</label>
    <input id="spdef-slider" type="range" min="1" max="300" step="1" value="120">
    <span id="spdef-label">120</span>
  </div>

  <script>
    const defenseSlider = document.getElementById('defense-slider');
    const defenseLabel  = document.getElementById('defense-label');
    const hpSlider      = document.getElementById('hp-slider');
    const hpLabel       = document.getElementById('hp-label');
    const spdefSlider   = document.getElementById('spdef-slider');
    const spdefLabel    = document.getElementById('spdef-label');
    // Plotly embeds the chart in the first div with class 'plotly-graph-div'
    const plotDiv = document.querySelector('.plotly-graph-div');

    let pending = null;

    function fetchData() {{
      const defense = parseFloat(defenseSlider.value);
      const hp      = parseFloat(hpSlider.value);
      const spdef   = parseFloat(spdefSlider.value);

      // Debounce: cancel any in-flight request before firing a new one
      if (pending) {{ pending.abort(); }}
      const controller = new AbortController();
      pending = controller;

      fetch(`/data?defense=${{defense}}&hp=${{hp}}&spdef=${{spdef}}`, {{ signal: controller.signal }})
        .then(r => r.json())
        .then(data => {{
          pending = null;
          // Plotly.react re-renders only what changed — no full redraw flicker
          Plotly.react(plotDiv, [
            {{ ...plotDiv.data[0], x: data.Physical.x, y: data.Physical.y, customdata: data.Physical.desc }},
            {{ ...plotDiv.data[1], x: data.Special.x,  y: data.Special.y,  customdata: data.Special.desc  }},
          ], plotDiv.layout);

        }})
        .catch(() => {{}});  // AbortError from cancelled requests is expected
    }}

    function updateHlines() {{
      const hp = parseFloat(hpSlider.value);
      Plotly.relayout(plotDiv, {{
        'shapes[0].y0': hp,           'shapes[0].y1': hp,
        'shapes[1].y0': Math.floor(hp / 2) + 1, 'shapes[1].y1': Math.floor(hp / 2) + 1,
      }});
    }}

    defenseSlider.addEventListener('input', () => {{ defenseLabel.textContent = defenseSlider.value; fetchData(); }});
    hpSlider.addEventListener('input',      () => {{ hpLabel.textContent      = hpSlider.value;      updateHlines();}});
    spdefSlider.addEventListener('input',   () => {{ spdefLabel.textContent   = spdefSlider.value;   fetchData(); }});
  </script>
</body>
</html>
"""
    return html.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = build_page()
            self._respond(200, "text/html; charset=utf-8", body)
        elif self.path.startswith("/data"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            defense = float(qs.get("defense", ["1.0"])[0])
            spdef = float(qs.get("spdef", ["1.0"])[0])
            hp = float(qs.get("hp", ["1.0"])[0])
            data = _get_damage_data()
            result_physical = get_adjusted_damage(*data["Physical"], defense=defense)
            result_special = get_adjusted_damage(*data["Special"], defense=spdef)
            result = {"Physical": result_physical, "Special": result_special, "HP": hp}
            body = orjson.dumps(result)
            self._respond(200, "application/json", body)
        else:
            self._respond(404, "text/plain", b"Not found")

    def _respond(self, status: int, content_type: str, body: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            pass  # client aborted the request (AbortController debounce)

    def log_message(self, format, *args):
        pass
        # print(f"  {selend toif.address_string()} - {format % args}")


if __name__ == "__main__":
    server = HTTPServer(("", PORT), Handler)
    server.timeout = 1
    print(f"Serving at http://localhost:{PORT}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

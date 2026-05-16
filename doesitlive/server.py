"""
Minimal HTTP server that generates an interactive Plotly chart and serves it to the browser.
Run with: python server.py
Then open: http://localhost:8080

Requires: pip install plotly numpy pandas
"""

import json
import urllib.parse
import numpy as np
import plotly.graph_objects as go
import pandas as pd

from http.server import BaseHTTPRequestHandler, HTTPServer

from doesitlive.adjust_damages import adjust_damage, Type

PORT = 8080


def load_damages(damage_file):
    df = pd.read_csv(damage_file)
    damages = df["avg_damage"].to_numpy()
    multipliers = df["multiplier"].to_numpy()
    desc = df["desc"].to_numpy(dtype=str)
    return damages, multipliers, desc


def compute_data(defense: float) -> dict:
    """Return trace data as a JSON-serialisable dict, parameterised by defense."""
    damages, multipliers, desc = load_damages("find_set/power_levels.csv")
    adjusted_damage = adjust_damage(
        damages, multipliers, defense, Type.NORMAL, Type.NORMAL, None
    )
    x = np.arange(len(adjusted_damage)).tolist()
    return {"x": x, "y": adjusted_damage.tolist(), "desc": desc.tolist()}


def generate_plot() -> str:
    """Return a self-contained Plotly chart as an HTML div string."""
    data = compute_data(defense=1.0)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["x"],
            y=data["y"],
            mode="lines",
            name="Damages",
            customdata=data["desc"],
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Does It Live?",
        xaxis_title="x",
        yaxis_title="y",
        hovermode="x unified",
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
    #defense-slider {{ flex: 1; }}
    #defense-label {{ min-width: 4ch; text-align: right; font-variant-numeric: tabular-nums; }}
  </style>
</head>
<body>
  <h1>Does It Live?</h1>
  <div class="chart-wrap">
    {chart_div}
  </div>
  <div class="slider-wrap">
    <label for="defense-slider">defense</label>
    <input id="defense-slider" type="range" min="1" max="300" step="1" value="120">
    <span id="defense-label">120</span>
  </div>

  <script>
    const slider = document.getElementById('defense-slider');
    const label  = document.getElementById('defense-label');
    // Plotly embeds the chart in the first div with class 'plotly-graph-div'
    const plotDiv = document.querySelector('.plotly-graph-div');

    let pending = null;

    slider.addEventListener('input', () => {{
      const defense = parseFloat(slider.value);
      label.textContent = defense.toFixed(2);

      // Debounce: cancel any in-flight request before firing a new one
      if (pending) {{ pending.abort(); }}
      const controller = new AbortController();
      pending = controller;

      fetch(`/data?defense=${{defense}}`, {{ signal: controller.signal }})
        .then(r => r.json())
        .then(data => {{
          pending = null;
          // Plotly.react re-renders only what changed — no full redraw flicker
          Plotly.react(plotDiv, [{{
            ...plotDiv.data[0],
            x: data.x,
            y: data.y,
            customdata: data.desc,
          }}], plotDiv.layout);
        }})
        .catch(() => {{}});  // AbortError from cancelled requests is expected
    }});
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
            defense = float(qs.get("scale", ["1.0"])[0])
            body = json.dumps(compute_data(defense)).encode()
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

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    server = HTTPServer(("", PORT), Handler)
    print(f"Serving at http://localhost:{PORT}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

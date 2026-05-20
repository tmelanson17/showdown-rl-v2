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

from doesitlive.adjust_damages import adjust_damage

# NOTE: scrape_stats depends on the requests + bs4 library
from find_set.scrape_stats import get_base_stats, get_type
from find_set.datatypes import PokemonStats, PokemonEvs
from find_set.nature import get_boost

PORT = 8080
_DAMAGE_DATA: dict | None = None
BASE_POKEMON: PokemonStats | None = None

#########################
### STAT HELPER CALCS (TODO: Move to separate file)
###########################
import math


# Use Champions EV base (0-32)
def stat_helper_calc(base: int, ev: int, iv: int = 31, level: int = 50):
    return math.floor(((2 * base + iv + ev) * level) / 100)


# Calculate the base stat based on EV's
def calculate_hp(base: int, ev: int, iv: int = 31, level: int = 50):
    return stat_helper_calc(base, ev, iv, level) + level + 10


def calculate_stat(
    base: int,
    ev: int,
    iv: int = 31,
    level: int = 50,
    nature_multiplier: float = 1.0,
):
    return math.floor((stat_helper_calc(base, ev, iv, level) + 5) * nature_multiplier)


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
    category = df["category"].to_numpy(dtype=str)
    types = df["type"].to_numpy(dtype=str)
    physical_idx = category == "Physical"
    special_idx = category == "Special"

    def create_custom_label(desc_str: str):
        return desc_str[: desc_str.find("vs.")]

    desc = df["desc"].apply(create_custom_label).to_numpy(dtype=str)

    return {
        "Physical": (
            damages[physical_idx],
            multipliers[physical_idx],
            desc[physical_idx],
            types[physical_idx].tolist(),
        ),
        "Special": (
            damages[special_idx],
            multipliers[special_idx],
            desc[special_idx],
            types[special_idx].tolist(),
        ),
    }


def get_adjusted_damage(damages, multipliers, desc, move_type, defense, type1, type2):
    adjusted_damage = adjust_damage(
        damages, multipliers, defense, move_type, type1, type2
    )
    sorted_indices = adjusted_damage.argsort()
    x = np.arange(len(adjusted_damage)).tolist()
    return {
        "x": x,
        "y": adjusted_damage[sorted_indices].tolist(),
        "desc": desc[sorted_indices].tolist(),
    }


def generate_plot() -> str:
    """Return a self-contained Plotly chart as an HTML div string."""
    global BASE_POKEMON
    assert BASE_POKEMON is not None
    data = load_damages("find_set/power_levels.csv")
    fig = go.Figure()

    for dfn, category in zip(
        (
            calculate_stat(BASE_POKEMON.base_stats.defn, 0),
            calculate_stat(BASE_POKEMON.base_stats.spdef, 0),
        ),
        ("Physical", "Special"),
    ):
        damage_adj = get_adjusted_damage(
            *data[category],
            defense=dfn,
            type1=BASE_POKEMON.type1,
            type2=BASE_POKEMON.type2,
        )

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
    hp = calculate_hp(BASE_POKEMON.base_stats.hp, 0)
    fig.add_hline(
        y=hp,
        line_dash="dot",
        label=dict(text="OHKO", textposition="top left"),
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
        yaxis=dict(range=[0, 300]),
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
    .marker-row {{ display: flex; align-items: center; gap: 0.5rem; }}
    .marker-row input[type=text] {{ flex: 1; padding: 0.25rem 0.5rem; font-size: 0.9rem; }}
    .marker-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
  </style>
</head>
<body>
  <h1>Does It Live?</h1>
  <div class="chart-wrap">
    {chart_div}
  </div>
  <div class="slider-wrap">
    <label for="pokemon-input">Pokemon</label>
    <input id="pokemon-input" type="text" placeholder="Garchomp">
    <button id="pokemon-submit">Submit</button>
  </div>
  <div class="slider-wrap">
    <label for="hp-slider">HP</label>
    <input id="hp-slider" type="range" min="0" max="32" step="1" value="0">
    <span id="hp-label">0</span>
  </div>
  <div class="slider-wrap">
    <label for="defense-slider">Defense</label>
    <input id="defense-slider" type="range" min="0" max="32" step="1" value="0">
    <span id="defense-label">0</span>
  </div>
  <div class="slider-wrap">
    <label for="spdef-slider">Sp. Defense</label>
    <input id="spdef-slider" type="range" min="0" max="32" step="1" value="0">
    <span id="spdef-label">0</span>
  </div>
  <div class="slider-wrap">
    <label for="nature-select">Nature</label>
    <select id="nature-select">
        <option value="Hardy" selected>Hardy (neutral)</option>
        <option value="Bold">Bold (+Def / −Atk)</option>
        <option value="Impish">Impish (+Def / −SpAtk)</option>
        <option value="Lax">Lax (+Def / −SpDef)</option>
        <option value="Relaxed">Relaxed (+Def / −Speed)</option>
        <option value="Lonely">Lonely (−Def / +Atk)</option>
        <option value="Hasty">Hasty (−Def / +Speed)</option>
        <option value="Mild">Mild (−Def / +SpAtk)</option>
        <option value="Gentle">Gentle (−Def / +SpDef)</option>
        <option value="Calm">Calm (+SpDef / −Atk)</option>
        <option value="Careful">Careful (+SpDef / −SpAtk)</option>
        <option value="Sassy">Sassy (+SpDef / −Speed)</option>
        <option value="Naughty">Naughty (−SpDef / +Atk)</option>
        <option value="Naive">Naive (−SpDef / +Speed)</option>
        <option value="Rash">Rash (−SpDef / +SpAtk)</option>
    </select>
  </div>

  <div style="width:90vw;max-width:900px;margin-top:1.5rem;display:flex;flex-direction:column;gap:0.5rem;">
    <button id="add-marker-btn">Add Marker</button>
    <div id="markers-container" style="display:flex;flex-direction:column;gap:0.5rem;"></div>
  </div>

  <script>
    const defenseSlider = document.getElementById('defense-slider');
    const defenseLabel  = document.getElementById('defense-label');
    const hpSlider      = document.getElementById('hp-slider');
    const hpLabel       = document.getElementById('hp-label');
    const spdefSlider   = document.getElementById('spdef-slider');
    const spdefLabel    = document.getElementById('spdef-label');
    const natureSelect  = document.getElementById('nature-select');

    // Plotly embeds the chart in the first div with class 'plotly-graph-div'
    const plotDiv = document.querySelector('.plotly-graph-div');

    // ── Markers ──────────────────────────────────────────────────────────────
    const MARKER_COLORS = ['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#16a085'];
    let markerSeq = 0;
    const activeMarkers = []; // {{id, text, color}}

    function buildMarkerData() {{
      const shapes = [];
      const hoverX = [], hoverY = [], hoverText = [], hoverColor = [];
      const maxY = Math.max(...plotDiv.data.slice(0, 2).flatMap(t => t.y || [0]));

      activeMarkers.forEach(({{text, color}}) => {{
        for (const trace of plotDiv.data.slice(0, 2)) {{
          if (!trace.customdata) continue;
          trace.customdata.forEach((desc, i) => {{
            if (!desc || !desc.toLowerCase().includes(text.toLowerCase())) return;
            const x = trace.x[i];
            shapes.push({{
              type: 'line', x0: x, x1: x, y0: 0, y1: 1, yref: 'paper',
              line: {{color, width: 1.5, dash: 'dot'}}
            }});
            // Invisible wide line segment to act as hover target for the vline
            hoverX.push(x, x, null);
            hoverY.push(0, maxY, null);
            hoverText.push(desc, desc, null);
            hoverColor.push(color, color, null);
          }});
        }}
      }});

      const hoverTrace = {{
        x: hoverX, y: hoverY, text: hoverText,
        mode: 'lines',
        line: {{color: 'rgba(0,0,0,0)', width: 12}},
        hovertemplate: '%{{text}}<extra></extra>',
        showlegend: false,
        type: 'scatter',
      }};
      return {{shapes, hoverTrace}};
    }}

    function applyMarkers() {{
      const hp0 = plotDiv.layout.shapes[0];
      const hp1 = plotDiv.layout.shapes[1];
      const {{shapes: mShapes, hoverTrace}} = buildMarkerData();
      Plotly.react(plotDiv, [...plotDiv.data.slice(0, 2), hoverTrace], {{
        ...plotDiv.layout,
        shapes: [hp0, hp1, ...mShapes],
        annotations: [],
      }});
    }}

    document.getElementById('add-marker-btn').addEventListener('click', () => {{
      const id = markerSeq++;
      const color = MARKER_COLORS[id % MARKER_COLORS.length];
      const container = document.getElementById('markers-container');
      const row = document.createElement('div');
      row.className = 'marker-row';
      row.dataset.markerId = id;

      const dot = document.createElement('div');
      dot.className = 'marker-dot';
      dot.style.background = color;

      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'Search move descriptions…';

      const submitBtn = document.createElement('button');
      submitBtn.textContent = 'Submit';

      const removeBtn = document.createElement('button');
      removeBtn.textContent = '✕';

      function submitMarker() {{
        const text = input.value.trim();
        if (!text) return;
        const existing = activeMarkers.findIndex(m => m.id === id);
        if (existing >= 0) activeMarkers[existing].text = text;
        else activeMarkers.push({{id, text, color}});
        applyMarkers();
      }}

      submitBtn.addEventListener('click', submitMarker);
      input.addEventListener('keydown', e => {{ if (e.key === 'Enter') submitMarker(); }});
      removeBtn.addEventListener('click', () => {{
        const idx = activeMarkers.findIndex(m => m.id === id);
        if (idx >= 0) activeMarkers.splice(idx, 1);
        row.remove();
        applyMarkers();
      }});

      row.append(dot, input, submitBtn, removeBtn);
      container.appendChild(row);
      input.focus();
    }});
    // ── End markers ───────────────────────────────────────────────────────────

    let pending = null;

    function fetchData() {{
      const defense = parseInt(defenseSlider.value);
      const hp      = parseInt(hpSlider.value);
      const spdef   = parseInt(spdefSlider.value);
      const name    = document.getElementById('pokemon-input').value.trim();
      const nature  = natureSelect.value;

      // Debounce: cancel any in-flight request before firing a new one
      if (pending) {{ pending.abort(); }}
      const controller = new AbortController();
      pending = controller;

      fetch(`/data?name=${{name}}&defense=${{defense}}&hp=${{hp}}&spdef=${{spdef}}&nature=${{nature}}`, {{ signal: controller.signal }})
        .then(r => r.json())
        .then(data => {{
          pending = null;
          // Plotly.react re-renders only what changed — no full redraw flicker
          Plotly.react(plotDiv, [
            {{ ...plotDiv.data[0], x: data.Physical.x, y: data.Physical.y, customdata: data.Physical.desc }},
            {{ ...plotDiv.data[1], x: data.Special.x,  y: data.Special.y,  customdata: data.Special.desc  }},
          ], plotDiv.layout);
          Plotly.relayout(plotDiv, {{
            'shapes[0].y0': data.HP,           'shapes[0].y1': data.HP,
            'shapes[1].y0': Math.floor(data.HP / 2) + 1, 'shapes[1].y1': Math.floor(data.HP / 2) + 1,
          }});
          applyMarkers();
        }})
        .catch(() => {{}});  // AbortError from cancelled requests is expected
    }}

    defenseSlider.addEventListener('input',  () => {{ defenseLabel.textContent = defenseSlider.value; fetchData(); }});
    hpSlider.addEventListener('input',       () => {{ hpLabel.textContent      = hpSlider.value;      fetchData(); }});
    spdefSlider.addEventListener('input',    () => {{ spdefLabel.textContent   = spdefSlider.value;   fetchData(); }});
    natureSelect.addEventListener('change',  () => fetchData());

    document.getElementById('pokemon-submit').addEventListener('click', () => {{ fetchData(); }});
  </script>
</body>
</html>
"""
    return html.encode()


class Handler(BaseHTTPRequestHandler):
    def update_base_stats(self, pokemon_name: str):
        global BASE_POKEMON
        if BASE_POKEMON is not None and BASE_POKEMON.name == pokemon_name:
            return
        stats = get_base_stats(pokemon_name)
        type1, type2 = get_type(pokemon_name)
        # TODO: Level needs to be not hard-coded (ok since this is Champions)
        BASE_POKEMON = PokemonStats(
            stats,
            nature="Docile",
            level=50,
            type1=type1,
            type2=type2,
            name=pokemon_name,
        )

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.update_base_stats("Garchomp")
            body = build_page()
            self._respond(200, "text/html; charset=utf-8", body)
        elif self.path.startswith("/data"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = qs.get("name", [None])[0]
            if name is not None:
                self.update_base_stats(name)
            defensive_evs = PokemonEvs(
                defn=int(qs.get("defense", ["0"])[0]),
                spdef=int(qs.get("spdef", ["0"])[0]),
                hp=int(qs.get("hp", ["0"])[0]),
            )
            global BASE_POKEMON
            assert BASE_POKEMON is not None
            nature_name = qs.get("nature", ["Hardy"])[0]
            def_mult = get_boost(nature_name, "def")
            spdef_mult = get_boost(nature_name, "spdef")
            hp = calculate_hp(BASE_POKEMON.base_stats.hp, defensive_evs.hp)
            defense = calculate_stat(
                BASE_POKEMON.base_stats.defn,
                defensive_evs.defn,
                nature_multiplier=def_mult,
            )
            spdef = calculate_stat(
                BASE_POKEMON.base_stats.spdef,
                defensive_evs.spdef,
                nature_multiplier=spdef_mult,
            )
            data = _get_damage_data()
            result_physical = get_adjusted_damage(
                *data["Physical"],
                defense=defense,
                type1=BASE_POKEMON.type1,
                type2=BASE_POKEMON.type2,
            )
            result_special = get_adjusted_damage(
                *data["Special"],
                defense=spdef,
                type1=BASE_POKEMON.type1,
                type2=BASE_POKEMON.type2,
            )
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

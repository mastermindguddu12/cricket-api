from flask import Flask, jsonify, request, render_template_string
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def clean(text):
    return re.sub(r'\s+', ' ', text).strip()

@app.route("/")
def home():
    return jsonify({"status": "Cricket API running", "dashboard": "/dashboard"})

@app.route("/matches")
def matches():
    try:
        url = "https://www.cricbuzz.com/cricket-match/live-scores"
        res = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")
        matches_list = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/live-cricket-scores/" in href:
                parts = href.strip("/").split("/")
                match_id = next((p for p in parts if p.isdigit()), None)
                title = a.get_text(strip=True)
                if match_id and title and len(title) > 5 and match_id not in seen:
                    seen.add(match_id)
                    matches_list.append({"id": match_id, "title": title})
        return jsonify({"matches": matches_list[:15]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/score")
def score():
    match_id = request.args.get("id")
    if not match_id:
        return jsonify({"error": "Missing match id"}), 400

    try:
        url = f"https://www.cricbuzz.com/live-cricket-scores/{match_id}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        # ----- Locate main content (skip sidebars) -----
        main = soup.find("div", class_=re.compile("cb-col-100 cb-col"))
        if not main:
            main = soup

        # ----- Title -----
        title = "N/A"
        h1 = soup.find("h1", class_=re.compile("cb-nav-hdr"))
        if h1:
            title = clean(h1.get_text())

        # ----- Innings scores (e.g., RR 150/3, MI 111/8) -----
        innings_scores = []
        # Method 1: look for divs with class "cb-min-scr" or "cb-score"
        score_divs = main.find_all("div", class_=re.compile("cb-min-scr|cb-score"))
        for div in score_divs:
            text = clean(div.get_text())
            # Find patterns like "RR 150/3" or "MI 111/8 (9.4)"
            matches = re.findall(r'([A-Z]{2,3})\s+(\d{1,3}/\d{1,3}(?:\s*\(\d{1,2}\.\d\))?)', text)
            for team, score in matches:
                innings_scores.append({"team": team, "score": score})
        # Deduplicate
        unique = []
        seen = set()
        for s in innings_scores:
            key = (s["team"], s["score"])
            if key not in seen:
                seen.add(key)
                unique.append(s)
        innings_scores = unique[:4]

        # Fallback: if empty, look for any score in the page (but ignore menus)
        if not innings_scores:
            all_text = clean(main.get_text())
            # Find all "Team score" patterns
            raw_matches = re.findall(r'([A-Z]{2,3})\s+(\d{1,3}/\d{1,3})', all_text)
            for team, score in raw_matches[:2]:
                innings_scores.append({"team": team, "score": score})

        # ----- Required runs / status (clean) -----
        required = "N/A"
        # Look for text containing "need" and "runs" inside main, excluding huge strings
        status_divs = main.find_all("div", class_=re.compile("cb-text|cb-status|cb-commentary-status"))
        for div in status_divs:
            text = clean(div.get_text())
            if "need" in text.lower() and "runs" in text.lower() and len(text) < 150:
                required = text
                break
        # If still not found, search any div with "need"
        if required == "N/A":
            for div in main.find_all("div"):
                text = clean(div.get_text())
                if "need" in text.lower() and "runs" in text.lower() and 20 < len(text) < 150:
                    required = text
                    break

        # ----- Run rates -----
        runrate = "N/A"
        rr_pattern = re.compile(r'CRR\s*:\s*[\d.]+|RRR\s*:\s*[\d.]+', re.I)
        for tag in main.find_all(["div", "span"]):
            text = clean(tag.get_text())
            m = rr_pattern.search(text)
            if m:
                runrate = m.group()
                break

        # ----- Current Batters (from tables with class cb-min-bat-rw) -----
        batters = []
        batter_rows = main.find_all("div", class_=re.compile("cb-min-bat-rw"))
        if not batter_rows:
            batter_rows = main.find_all("tr", class_=re.compile("cb-min-bat"))
        for row in batter_rows[:2]:
            cells = [clean(c.get_text()) for c in row.find_all(["div", "span", "td"]) if clean(c.get_text())]
            # Expected: [Name, Runs, Balls, 4s, 6s, SR]
            if len(cells) >= 2:
                batters.append({
                    "name": cells[0],
                    "runs": cells[1] if len(cells) > 1 else "N/A",
                    "balls": cells[2] if len(cells) > 2 else "N/A",
                    "fours": cells[3] if len(cells) > 3 else "N/A",
                    "sixes": cells[4] if len(cells) > 4 else "N/A",
                    "sr": cells[5] if len(cells) > 5 else "N/A"
                })
        # Fallback: look for any div with batter stats
        if not batters:
            for div in main.find_all("div", class_=re.compile("cb-col")):
                text = clean(div.get_text())
                if re.search(r'\d+\s*\(\d+\)', text) and "batter" not in text.lower():
                    parts = text.split()
                    if len(parts) >= 2:
                        batters.append({"name": parts[0], "runs": parts[1]})

        # ----- Current Bowlers -----
        bowlers = []
        bowler_rows = main.find_all("div", class_=re.compile("cb-min-bwl-rw"))
        if not bowler_rows:
            bowler_rows = main.find_all("tr", class_=re.compile("cb-min-bwl"))
        for row in bowler_rows[:2]:
            cells = [clean(c.get_text()) for c in row.find_all(["div", "span", "td"]) if clean(c.get_text())]
            # Expected: [Name, Overs, Runs, Wickets, Economy]
            if len(cells) >= 3:
                bowlers.append({
                    "name": cells[0],
                    "overs": cells[1] if len(cells) > 1 else "N/A",
                    "runs": cells[2] if len(cells) > 2 else "N/A",
                    "wickets": cells[3] if len(cells) > 3 else "N/A",
                    "economy": cells[4] if len(cells) > 4 else "N/A"
                })

        # ----- Partnership -----
        partnership = "N/A"
        for div in main.find_all("div"):
            text = clean(div.get_text())
            if re.match(r"P'?SHIP", text, re.I) and len(text) < 100:
                partnership = text
                break

        # ----- Last Wicket -----
        last_wicket = "N/A"
        for div in main.find_all("div"):
            text = clean(div.get_text())
            if "last wkt" in text.lower() and len(text) < 200:
                last_wicket = text
                break

        # ----- Reviews Remaining -----
        reviews = {"RR": "N/A", "MI": "N/A"}
        reviews_div = main.find("div", class_=re.compile("cb-reviews"))
        if reviews_div:
            rev_text = clean(reviews_div.get_text())
            rr_match = re.search(r'RR\s*-\s*(\d+)\s*of\s*(\d+)', rev_text, re.I)
            mi_match = re.search(r'MI\s*-\s*(\d+)\s*of\s*(\d+)', rev_text, re.I)
            if rr_match:
                reviews["RR"] = f"{rr_match.group(1)}/{rr_match.group(2)}"
            if mi_match:
                reviews["MI"] = f"{mi_match.group(1)}/{mi_match.group(2)}"

        # ----- Win Probability -----
        win_prob = "N/A"
        prob_div = main.find("div", class_=re.compile("cb-win-prob"))
        if prob_div:
            prob_text = clean(prob_div.get_text())
            m = re.search(r'(\d{1,3}\.?\d*%)', prob_text)
            if m:
                win_prob = m.group(1)
        else:
            # Look for text like "RR 97%"
            for div in main.find_all("div"):
                text = clean(div.get_text())
                m = re.search(r'([A-Z]{2,3})\s+(\d{1,3}\.?\d*%)', text)
                if m and m.group(1) in ["RR", "MI"]:
                    win_prob = m.group(2)
                    break

        # ----- Recent balls (commentary snippet) -----
        recent_balls = "N/A"
        comm_div = main.find("div", class_=re.compile("cb-commentary|cb-min-comm"))
        if comm_div:
            recent_balls = clean(comm_div.get_text())[:400]
        else:
            # Fallback: find any div with ball-by-ball numbers
            for div in main.find_all("div"):
                text = clean(div.get_text())
                if re.search(r'\d\.\d\s+[46W]', text):
                    recent_balls = text[:400]
                    break

        # ----- All scores (fallback) -----
        all_scores = list(dict.fromkeys(re.findall(r'\b\d{1,3}/\d{1,3}(?:\s*\(\d{1,2}\.\d\))?', res.text)))[:5]

        result = {
            "title": title,
            "innings_scores": innings_scores,
            "required": required,
            "runrate": runrate,
            "batters": batters,
            "bowlers": bowlers,
            "partnership": partnership,
            "last_wicket": last_wicket,
            "reviews": reviews,
            "win_probability": win_prob,
            "recent_balls": recent_balls,
            "all_scores": all_scores,
        }
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------- DASHBOARD HTML (clean layout) -------------------
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏏 Live Cricket Score Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        .live-pulse { animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
        .card { transition: transform 0.2s; }
        .card:hover { transform: translateY(-2px); }
    </style>
</head>
<body class="bg-gray-900 text-white">
    <div class="container mx-auto px-4 py-6 max-w-7xl">
        <!-- Header -->
        <div class="flex justify-between items-center mb-6 flex-wrap gap-3">
            <div>
                <h1 class="text-3xl font-bold bg-gradient-to-r from-orange-400 to-red-500 bg-clip-text text-transparent">
                    <i class="fas fa-cricket-ball text-orange-400"></i> Live Cricket Dashboard
                </h1>
                <p class="text-gray-400 text-sm">Real-time from Cricbuzz</p>
            </div>
            <div class="flex gap-3">
                <button id="refreshBtn" class="bg-orange-600 hover:bg-orange-700 px-4 py-2 rounded-lg flex items-center gap-2">
                    <i class="fas fa-sync-alt"></i> Refresh
                </button>
                <button id="autoToggle" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded-lg flex items-center gap-2">
                    <i class="fas fa-play"></i> Auto (10s)
                </button>
            </div>
        </div>

        <!-- Match Selector -->
        <div class="bg-gray-800 rounded-xl p-4 mb-6 shadow-lg">
            <label class="block text-sm text-gray-300 mb-1">Select Match</label>
            <select id="matchSelect" class="w-full md:w-96 bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-white focus:ring-2 focus:ring-orange-500">
                <option>Loading matches...</option>
            </select>
            <div id="updateTime" class="text-xs text-gray-500 mt-2"></div>
        </div>

        <!-- Main Scorecard Grid -->
        <div id="scorecardContainer" class="space-y-6">
            <div class="text-center py-12 text-gray-500">
                <i class="fas fa-spinner fa-spin text-2xl mb-2"></i>
                <p>Select a match to see live score</p>
            </div>
        </div>
    </div>

    <script>
        let autoRefresh = false;
        let interval = null;
        let currentMatchId = null;

        const matchSelect = document.getElementById('matchSelect');
        const container = document.getElementById('scorecardContainer');
        const updateTimeSpan = document.getElementById('updateTime');

        async function loadMatches() {
            try {
                const res = await fetch('/matches');
                const data = await res.json();
                if (data.matches && data.matches.length) {
                    matchSelect.innerHTML = '<option value="">-- Select a match --</option>' +
                        data.matches.map(m => `<option value="${m.id}">${m.title}</option>`).join('');
                    if (!currentMatchId && data.matches[0]) {
                        matchSelect.value = data.matches[0].id;
                        loadScore(data.matches[0].id);
                    }
                } else {
                    matchSelect.innerHTML = '<option>No live matches</option>';
                }
            } catch (err) {
                matchSelect.innerHTML = '<option>Error loading matches</option>';
            }
        }

        async function loadScore(matchId) {
            if (!matchId) return;
            currentMatchId = matchId;
            try {
                const res = await fetch(`/score?id=${matchId}`);
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                renderScorecard(data);
                updateTimeSpan.innerHTML = `<i class="fas fa-clock"></i> Updated: ${new Date().toLocaleTimeString()}`;
            } catch (err) {
                container.innerHTML = `<div class="bg-red-900/50 border border-red-700 rounded-xl p-6 text-center">Failed to load: ${err.message}</div>`;
            }
        }

        function renderScorecard(d) {
            // Helper for innings scores
            const inningsHtml = d.innings_scores && d.innings_scores.length
                ? `<div class="flex gap-8 justify-center flex-wrap">${d.innings_scores.map(inn => 
                    `<div class="text-center"><div class="text-2xl font-bold">${inn.team}</div><div class="text-3xl font-mono font-bold text-orange-300">${inn.score}</div></div>`
                  ).join('')}</div>`
                : `<div class="text-center text-gray-400">Score: N/A</div>`;

            // Batters table
            const battersHtml = d.batters && d.batters.length
                ? `<table class="w-full text-sm"><thead><tr class="border-b border-gray-700"><th>Batter</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead><tbody>
                    ${d.batters.map(b => `<tr><td class="font-medium">${b.name}</td><td>${b.runs}</td><td>${b.balls}</td><td>${b.fours}</td><td>${b.sixes}</td><td>${b.sr}</td></tr>`).join('')}
                   </tbody></table>`
                : `<div class="text-gray-500 text-center py-3">No batter data</div>`;

            // Bowlers table
            const bowlersHtml = d.bowlers && d.bowlers.length
                ? `<table class="w-full text-sm"><thead><tr class="border-b border-gray-700"><th>Bowler</th><th>O</th><th>R</th><th>W</th><th>Econ</th></tr></thead><tbody>
                    ${d.bowlers.map(b => `<tr><td class="font-medium">${b.name}</td><td>${b.overs}</td><td>${b.runs}</td><td>${b.wickets}</td><td>${b.economy}</td></tr>`).join('')}
                   </tbody></table>`
                : `<div class="text-gray-500 text-center py-3">No bowler data</div>`;

            container.innerHTML = `
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <!-- Left + Middle (main info) -->
                    <div class="lg:col-span-2 space-y-6">
                        <!-- Title & Win Prob -->
                        <div class="bg-gray-800/80 rounded-xl p-5 card">
                            <div class="flex justify-between items-start flex-wrap gap-2">
                                <h2 class="text-xl font-bold"><i class="fas fa-trophy text-yellow-500"></i> ${d.title}</h2>
                                ${d.win_probability !== 'N/A' ? `<span class="bg-green-900/50 text-green-300 px-3 py-1 rounded-full text-sm"><i class="fas fa-chart-line"></i> ${d.win_probability}</span>` : ''}
                            </div>
                            <div class="mt-4">${inningsHtml}</div>
                            <div class="mt-3 flex justify-between text-gray-300 text-sm">
                                <span><i class="fas fa-chart-simple"></i> ${d.runrate}</span>
                                <span class="text-orange-400"><i class="fas fa-bullhorn"></i> ${d.required}</span>
                            </div>
                        </div>

                        <!-- Partnership & Last Wicket -->
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            ${d.partnership !== 'N/A' ? `<div class="bg-gray-800/50 rounded-xl p-4 card"><h3 class="text-sm uppercase text-gray-400"><i class="fas fa-handshake"></i> Partnership</h3><p class="text-lg font-mono mt-1">${d.partnership}</p></div>` : ''}
                            ${d.last_wicket !== 'N/A' ? `<div class="bg-gray-800/50 rounded-xl p-4 card"><h3 class="text-sm uppercase text-gray-400"><i class="fas fa-skull"></i> Last Wicket</h3><p class="text-sm mt-1">${d.last_wicket}</p></div>` : ''}
                        </div>

                        <!-- Batters & Bowlers -->
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div class="bg-gray-800/50 rounded-xl p-4 card"><h3 class="text-lg font-semibold mb-3"><i class="fas fa-bat"></i> Current Batters</h3>${battersHtml}</div>
                            <div class="bg-gray-800/50 rounded-xl p-4 card"><h3 class="text-lg font-semibold mb-3"><i class="fas fa-baseball-ball"></i> Current Bowlers</h3>${bowlersHtml}</div>
                        </div>
                    </div>

                    <!-- Right sidebar -->
                    <div class="space-y-6">
                        <div class="bg-gray-800/50 rounded-xl p-4 card">
                            <h3 class="text-sm uppercase text-gray-400"><i class="fas fa-microphone-alt"></i> Reviews Remaining</h3>
                            <div class="flex justify-between mt-2"><span>RR: ${d.reviews?.RR || 'N/A'}</span><span>MI: ${d.reviews?.MI || 'N/A'}</span></div>
                        </div>
                        <div class="bg-gray-800/50 rounded-xl p-4 card">
                            <h3 class="text-sm uppercase text-gray-400"><i class="fas fa-history"></i> Recent Action</h3>
                            <div class="text-sm font-mono mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap bg-gray-900 p-2 rounded">${d.recent_balls || 'N/A'}</div>
                        </div>
                        ${d.all_scores && d.all_scores.length ? `<div class="bg-gray-800/50 rounded-xl p-4 card"><h3 class="text-sm uppercase text-gray-400"><i class="fas fa-chart-bar"></i> All Scores</h3><div class="flex flex-wrap gap-2 mt-2">${d.all_scores.map(s => `<span class="bg-gray-700 px-2 py-1 rounded text-xs">${s}</span>`).join('')}</div></div>` : ''}
                    </div>
                </div>
            `;
        }

        matchSelect.addEventListener('change', (e) => {
            if (e.target.value) loadScore(e.target.value);
        });

        document.getElementById('refreshBtn').addEventListener('click', () => {
            if (currentMatchId) loadScore(currentMatchId);
        });

        const autoBtn = document.getElementById('autoToggle');
        autoBtn.addEventListener('click', () => {
            autoRefresh = !autoRefresh;
            if (autoRefresh) {
                if (interval) clearInterval(interval);
                interval = setInterval(() => { if (currentMatchId) loadScore(currentMatchId); }, 10000);
                autoBtn.innerHTML = '<i class="fas fa-pause"></i> Auto (10s)';
                autoBtn.classList.remove('bg-gray-700');
                autoBtn.classList.add('bg-orange-600');
            } else {
                if (interval) clearInterval(interval);
                autoBtn.innerHTML = '<i class="fas fa-play"></i> Auto (10s)';
                autoBtn.classList.remove('bg-orange-600');
                autoBtn.classList.add('bg-gray-700');
            }
        });

        loadMatches();
    </script>
</body>
</html>
'''

@app.route("/dashboard")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

@app.route("/")
def home():
    return jsonify({
        "status": "Cricket API is running ✅",
        "endpoints": {
            "live_matches": "/matches",
            "match_score": "/score?id=MATCH_ID",
            "debug": "/debug?id=MATCH_ID"
        }
    })

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
                    matches_list.append({
                        "id": match_id,
                        "title": title,
                        "score_url": f"/score?id={match_id}"
                    })
        return jsonify({"matches": matches_list[:15], "count": len(matches_list)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/score")
def score():
    match_id = request.args.get("id")
    if not match_id:
        return jsonify({"error": "Provide match id. Example: /score?id=12345"}), 400

    try:
        url = f"https://www.cricbuzz.com/live-cricket-scores/{match_id}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        html = res.text

        # --- Title ---
        title = "N/A"
        for tag in soup.find_all(["h1","h2","h3"]):
            t = tag.get_text(strip=True)
            if len(t) > 8:
                title = t
                break

        # --- Smart score extraction ---
        # Method 1: find any element with score pattern like "123/4 (20.0)"
        livescore = "N/A"
        score_pattern = re.compile(r'\d+/\d+\s*\(\d+\.?\d*\)|\d+/\d+')
        for tag in soup.find_all(["div","span"]):
            text = tag.get_text(strip=True)
            if score_pattern.search(text) and len(text) < 60:
                livescore = text
                break

        # Method 2: look for any cb- class with score data
        if livescore == "N/A":
            for tag in soup.find_all(True):
                classes = " ".join(tag.get("class", []))
                if "scr" in classes or "score" in classes.lower():
                    text = tag.get_text(strip=True)
                    if score_pattern.search(text) and len(text) < 80:
                        livescore = text
                        break

        # --- Status update ---
        update = "N/A"
        # Look for common status texts
        status_keywords = ["won", "lost", "draw", "live", "innings", "over", "wicket", "stumps", "tea", "lunch"]
        for tag in soup.find_all(["div","span","p"]):
            classes = " ".join(tag.get("class", []))
            text = tag.get_text(strip=True)
            if any(kw in text.lower() for kw in status_keywords) and len(text) < 150:
                if any(x in classes for x in ["cb-text", "cb-lv", "cb-scr", "status"]):
                    update = text
                    break

        # Fallback status
        if update == "N/A":
            for tag in soup.find_all(["div","span"]):
                text = tag.get_text(strip=True)
                if any(kw in text.lower() for kw in ["won by", "need", "trail", "lead"]) and len(text) < 150:
                    update = text
                    break

        # --- Run rate ---
        runrate = "N/A"
        rr_pattern = re.compile(r'CRR\s*:\s*[\d.]+|RRR\s*:\s*[\d.]+', re.IGNORECASE)
        for tag in soup.find_all(["div","span"]):
            text = tag.get_text(strip=True)
            match = rr_pattern.search(text)
            if match:
                runrate = match.group()
                break

        # --- Batters: look for scorecard rows ---
        batters = []
        batter_pattern = re.compile(r'^\d+$')
        rows = soup.find_all("div", class_=lambda c: c and any(x in " ".join(c) for x in ["cb-min-inf","cb-min-bat","cb-bwl","cb-scrd"]))
        for row in rows[:4]:
            cols = [c.get_text(strip=True) for c in row.find_all(["div","span","td"]) if c.get_text(strip=True)]
            if len(cols) >= 2:
                batters.append(cols)

        batter_data = {}
        if len(batters) > 0:
            batter_data["batterone"] = batters[0][0] if batters[0] else "N/A"
            batter_data["batsmanonerun"] = batters[0][1] if len(batters[0]) > 1 else "N/A"
            batter_data["batsmanoneball"] = batters[0][2] if len(batters[0]) > 2 else "N/A"
        if len(batters) > 1:
            batter_data["battertwo"] = batters[1][0] if batters[1] else "N/A"
            batter_data["batsmantworun"] = batters[1][1] if len(batters[1]) > 1 else "N/A"
            batter_data["batsmantwoball"] = batters[1][2] if len(batters[1]) > 2 else "N/A"

        # --- All scores fallback ---
        all_scores = []
        for tag in soup.find_all(["div","span"]):
            text = tag.get_text(strip=True)
            if score_pattern.search(text) and 3 < len(text) < 50:
                if text not in all_scores:
                    all_scores.append(text)
            if len(all_scores) >= 6:
                break

        result = {
            "title": title,
            "update": update,
            "livescore": livescore,
            "runrate": runrate,
            "all_scores": all_scores,
        }
        result.update(batter_data)
        return jsonify(result)

    except requests.Timeout:
        return jsonify({"error": "Timed out. Try again."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/debug")
def debug():
    match_id = request.args.get("id", "")
    if not match_id:
        return jsonify({"error": "Provide ?id=MATCH_ID"}), 400
    try:
        url = f"https://www.cricbuzz.com/live-cricket-scores/{match_id}"
        res = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")
        classes = set()
        for tag in soup.find_all(True):
            for c in tag.get("class", []):
                if "cb-" in c:
                    classes.add(c)
        # also grab all text that looks like a score
        score_texts = []
        score_pattern = re.compile(r'\d+/\d+')
        for tag in soup.find_all(["div","span"]):
            text = tag.get_text(strip=True)
            if score_pattern.search(text) and len(text) < 60:
                score_texts.append({
                    "class": " ".join(tag.get("class",[])),
                    "text": text
                })
        return jsonify({
            "status_code": res.status_code,
            "page_title": soup.title.string if soup.title else "N/A",
            "score_elements_found": score_texts[:15],
            "cb_classes_found": sorted(list(classes))[:80]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

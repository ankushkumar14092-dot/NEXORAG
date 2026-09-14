/**
 * Vercel serverless — fetch YouTube captions (bypasses Render datacenter IP).
 * Note: some cloud IPs still get 403; then upload the video file instead.
 */
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

function segmentsFromJson3(payload) {
  const out = [];
  for (const event of payload.events || []) {
    const segs = event.segs || [];
    if (!segs.length) continue;
    const text = segs
      .map((s) => s.utf8 || "")
      .join("")
      .replace(/\n/g, " ")
      .trim();
    if (!text) continue;
    const startMs = Number(event.tStartMs || 0);
    const durMs = Number(event.dDurationMs || 0);
    const start = startMs / 1000;
    const end = durMs ? (startMs + durMs) / 1000 : start + 2;
    out.push({ start, end, text });
  }
  return out;
}

async function player(client, videoId) {
  const body = {
    context: { client: { ...client, hl: "en", gl: "US" } },
    videoId,
  };
  if (String(client.clientName || "").includes("EMBED") || client.clientName === "TVHTML5") {
    body.context.client.clientScreen = "EMBED";
    body.context.thirdParty = { embedUrl: "https://www.youtube.com/" };
  }
  const resp = await fetch(
    "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
    {
      method: "POST",
      headers: {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        Origin: "https://www.youtube.com",
        Referer: `https://www.youtube.com/watch?v=${videoId}`,
      },
      body: JSON.stringify(body),
    }
  );
  const text = await resp.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    data = null;
  }
  return { status: resp.status, data, raw: text.slice(0, 200) };
}

async function fetchViaInnertube(videoId) {
  const clients = [
    { clientName: "ANDROID", clientVersion: "20.10.38" },
    {
      clientName: "IOS",
      clientVersion: "20.10.4",
      deviceModel: "iPhone16,2",
      osName: "iOS",
      osVersion: "17.5",
    },
    { clientName: "ANDROID_VR", clientVersion: "1.60.19" },
    { clientName: "TVHTML5", clientVersion: "7.20240701.16.00" },
  ];
  const errors = [];
  for (const client of clients) {
    const { status, data, raw } = await player(client, videoId);
    if (status !== 200 || !data) {
      errors.push(`${client.clientName}: HTTP ${status} ${raw}`);
      continue;
    }
    const playability = data?.playabilityStatus?.status;
    if (playability && playability !== "OK") {
      errors.push(
        `${client.clientName}: playability ${playability} ${data?.playabilityStatus?.reason || ""}`
      );
      continue;
    }
    const title =
      data?.videoDetails?.title ||
      data?.microformat?.playerMicroformatRenderer?.title?.simpleText ||
      videoId;
    const tracks =
      data?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
    if (!tracks.length) {
      errors.push(`${client.clientName}: no captionTracks`);
      continue;
    }
    const preferred = ["en", "hi", "en-US", "en-GB", "en-IN"];
    tracks.sort((a, b) => {
      const ca = (a.languageCode || "").toLowerCase();
      const cb = (b.languageCode || "").toLowerCase();
      const pa = preferred.includes(ca) ? preferred.indexOf(ca) : 99;
      const pb = preferred.includes(cb) ? preferred.indexOf(cb) : 99;
      const ga = a.kind === "asr" ? 1 : 0;
      const gb = b.kind === "asr" ? 1 : 0;
      return pa - pb || ga - gb;
    });
    for (const track of tracks) {
      let base = track.baseUrl || "";
      if (!base) continue;
      if (base.includes("fmt=")) base = base.replace(/fmt=[^&]+/, "fmt=json3");
      else base += (base.includes("?") ? "&" : "?") + "fmt=json3";
      const tt = await fetch(base, {
        headers: {
          "User-Agent": UA,
          "Accept-Language": "en-US,en;q=0.9",
          Referer: `https://www.youtube.com/watch?v=${videoId}`,
        },
      });
      if (!tt.ok) {
        errors.push(`timedtext ${track.languageCode}: HTTP ${tt.status}`);
        continue;
      }
      const payload = await tt.json();
      const segments = segmentsFromJson3(payload);
      if (segments.length) {
        return { title, segments, method: `innertube_${client.clientName}` };
      }
    }
    errors.push(`${client.clientName}: empty timedtext`);
  }
  throw new Error(errors.slice(0, 4).join(" | ") || "all clients failed");
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ detail: "GET only" });

  const v = String(req.query.v || "").trim();
  if (!/^[A-Za-z0-9_-]{11}$/.test(v)) {
    return res.status(400).json({ detail: "Invalid YouTube video id" });
  }
  try {
    const data = await fetchViaInnertube(v);
    return res.status(200).json({
      ok: true,
      video_id: v,
      title: data.title,
      segment_count: data.segments.length,
      segments: data.segments,
      method: data.method,
    });
  } catch (err) {
    return res.status(502).json({
      detail: `Caption proxy failed: ${err && err.message ? err.message : err}`,
      hint: "YouTube blocks many cloud IPs. Upload the video file instead, or ingest on a local machine.",
    });
  }
}

import { defineConfig } from "vite";
import { appendFileSync } from "node:fs";

/*
    Audio files here are large and are sometimes still being
    written when the server is running. Watching them gained
    nothing and cost a crash: Vite's watcher took an EBUSY on a
    track mid-copy and took the whole dev server down with it.
    They are served normally, just not watched for changes.
*/

/*
    A dev-only sink for gameplay telemetry. The page posts a
    batch of miss reports here and they are appended to
    telemetry.jsonl, so what happened during a real session on
    real hardware can be read back afterwards rather than
    guessed at.
*/
function telemetrySink() {
    return {
        name: "telemetry-sink",
        configureServer(server) {
            server.middlewares.use("/telemetry", (req, res) => {
                if (req.method !== "POST") {
                    res.statusCode = 405;
                    res.end();
                    return;
                }

                let body = "";

                req.on("data", chunk => {
                    body += chunk;

                    /*
                        A runaway client should not be able to
                        fill the disk.
                    */
                    if (body.length > 1_000_000) {
                        req.destroy();
                    }
                });

                req.on("end", () => {
                    try {
                        const rows = JSON.parse(body);

                        for (const row of rows) {
                            appendFileSync(
                                "telemetry.jsonl",
                                JSON.stringify(row) + "\n"
                            );
                        }
                    } catch (error) {
                        console.warn("Bad telemetry:", error.message);
                    }

                    res.statusCode = 204;
                    res.end();
                });
            });
        }
    };
}

export default defineConfig({
    plugins: [telemetrySink()],
    server: {
        watch: {
            ignored: [
                "**/*.wav",
                "**/*.mp3",
                "**/*.ogg",
                "**/*.mp4",
                "**/*.webm",
                "**/telemetry.jsonl"
            ]
        }
    }
});

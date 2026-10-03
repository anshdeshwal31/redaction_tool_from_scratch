// Loopback client run inside the Philter container (java source launcher, JDK stdlib only).
// Usage: java Explain.java status            -> prints the /api/status body
//        java Explain.java explain <policy>  -> stdin: one base64 UTF-8 text per line;
//                                               stdout: one base64 /api/explain JSON body per line.
// The container runs with --network none, so only loopback exists. A request that takes over 60 s
// (the service occasionally hangs under repeated load) ends the client; the adapter restarts the
// container and resumes after the last delivered result. No PII in this file.
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;

public class Explain {
    public static void main(String[] args) throws Exception {
        HttpClient c = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
        String base = "http://127.0.0.1:8080";
        if (args[0].equals("status")) {
            HttpResponse<String> r = c.send(HttpRequest.newBuilder(URI.create(base + "/api/status")).GET().build(),
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            System.out.println(r.statusCode() + " " + r.body());
            return;
        }
        String policy = URLEncoder.encode(args[1], StandardCharsets.UTF_8);
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        Base64.Decoder dec = Base64.getDecoder();
        Base64.Encoder enc = Base64.getEncoder();
        String line;
        while ((line = in.readLine()) != null) {
            String text = new String(dec.decode(line.trim()), StandardCharsets.UTF_8);
            HttpRequest req = HttpRequest.newBuilder(URI.create(base + "/api/explain?p=" + policy + "&c=redactor"))
                    .header("Content-Type", "text/plain; charset=utf-8")
                    .timeout(Duration.ofSeconds(60))
                    .POST(HttpRequest.BodyPublishers.ofString(text, StandardCharsets.UTF_8)).build();
            // the service can answer 5xx while busy; a successful answer is deterministic, so retry with backoff
            HttpResponse<String> r = null;
            long t0 = System.nanoTime();
            for (int attempt = 0; attempt < 6; attempt++) {
                r = c.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
                if (r.statusCode() < 500) break;
                Thread.sleep(500L << attempt);
            }
            if (r.statusCode() != 200) {
                System.err.println("explain failed: HTTP " + r.statusCode());   // the status code only, never the text
                System.exit(3);
            }
            System.err.println("ms " + (System.nanoTime() - t0) / 1000000);   // timing only
            System.out.println(enc.encodeToString(r.body().getBytes(StandardCharsets.UTF_8)));
            System.out.flush();   // each finished result is delivered even if a later request hangs
        }
    }
}

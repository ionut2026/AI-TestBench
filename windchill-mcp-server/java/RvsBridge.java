import com.mks.api.*;
import com.mks.api.response.*;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;

/**
 * Line-delimited JSON bridge between the Node MCP server and the Windchill RV&S Java API.
 * Request:  {"id":1,"app":"im","cmd":"issues","options":[["fields","ID,Summary"],["batch"]],"selection":["123"],"limit":50}
 * Response: {"id":1,"ok":true,"result":{...}} or {"id":1,"ok":false,"error":"..."}
 */
public class RvsBridge {
    private static final PrintStream OUT;
    static {
        try {
            OUT = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            throw new RuntimeException(e);
        }
    }

    private static IntegrationPoint ip;
    private static Session session;
    private static String hostname = System.getenv("RVS_HOSTNAME");
    private static Integer port = System.getenv("RVS_PORT") != null && !System.getenv("RVS_PORT").isEmpty()
            ? Integer.valueOf(System.getenv("RVS_PORT")) : null;

    public static void main(String[] args) throws Exception {
        // Keep the API from writing to stdout, which is reserved for the protocol.
        System.setOut(System.err);
        connect();
        ExecutorService pool = Executors.newFixedThreadPool(8);
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = in.readLine()) != null) {
            final String l = line.trim();
            if (l.isEmpty()) continue;
            pool.submit(() -> handle(l));
        }
        pool.shutdown();
        pool.awaitTermination(30, TimeUnit.SECONDS);
        try { ip.release(); } catch (Exception ignored) { }
        System.exit(0);
    }

    private static synchronized void connect() throws APIException {
        if (session != null) return;
        int major = 4, minor = 16;
        ip = IntegrationPointFactory.getInstance().createLocalIntegrationPoint(major, minor);
        ip.setAutoStartIntegrityClient(true);
        session = ip.getCommonSession();
    }

    private static void handle(String line) {
        Object id = null;
        try {
            Map<String, Object> req = Json.parseObject(line);
            id = req.get("id");
            Map<String, Object> result = execute(req);
            send(id, true, result, null);
        } catch (APIException e) {
            send(id, false, null, describe(e));
        } catch (Throwable t) {
            send(id, false, null, t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    private static String describe(APIException e) {
        StringBuilder sb = new StringBuilder();
        String msg = e.getMessage();
        if (msg != null) sb.append(msg);
        try {
            Response r = e.getResponse();
            if (r != null) {
                WorkItemIterator it = r.getWorkItems();
                while (it.hasNext()) {
                    try { it.next(); } catch (APIException inner) {
                        if (inner.getMessage() != null && sb.indexOf(inner.getMessage()) < 0) sb.append(" | ").append(inner.getMessage());
                    }
                }
            }
        } catch (Exception ignored) { }
        return sb.length() == 0 ? e.getClass().getSimpleName() : sb.toString();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> execute(Map<String, Object> req) throws APIException {
        String app = str(req.getOrDefault("app", "im"));
        String cmdName = str(req.get("cmd"));
        if (cmdName == null) throw new IllegalArgumentException("Missing 'cmd'");
        if ("__ping".equals(cmdName)) {
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("pong", true);
            r.put("hostname", hostname);
            r.put("port", port);
            return r;
        }
        int limit = req.get("limit") instanceof Number ? ((Number) req.get("limit")).intValue() : -1;
        int fieldMax = req.get("maxFieldLength") instanceof Number ? ((Number) req.get("maxFieldLength")).intValue() : -1;

        Command cmd = new Command(app, cmdName);
        Object opts = req.get("options");
        if (opts instanceof List) {
            for (Object o : (List<Object>) opts) {
                List<Object> pair = (List<Object>) o;
                String name = str(pair.get(0));
                if (pair.size() > 1 && pair.get(1) != null) cmd.addOption(new Option(name, str(pair.get(1))));
                else cmd.addOption(new Option(name));
            }
        }
        Object sel = req.get("selection");
        if (sel instanceof List) {
            for (Object s : (List<Object>) sel) cmd.addSelection(str(s));
        }

        CmdRunner runner = session.createCmdRunner();
        try {
            if (hostname != null && !hostname.isEmpty()) runner.setDefaultHostname(hostname);
            if (port != null) runner.setDefaultPort(port);
            Response resp = limit > 0 ? runner.executeWithInterim(cmd, false) : runner.execute(cmd);
            if (limit <= 0) throwIfFailed(resp);
            Map<String, Object> out = new LinkedHashMap<>();
            List<Object> items = new ArrayList<>();
            boolean truncated = false;
            WorkItemIterator it = resp.getWorkItems();
            while (it.hasNext()) {
                if (limit > 0 && items.size() >= limit) { truncated = true; break; }
                WorkItem wi;
                try {
                    wi = it.next();
                } catch (APIException e) {
                    Map<String, Object> err = new LinkedHashMap<>();
                    err.put("error", describe(e));
                    items.add(err);
                    continue;
                }
                items.add(workItem(wi, fieldMax));
            }
            if (truncated) {
                try { runner.interrupt(); } catch (Exception ignored) { }
            }
            out.put("workItems", items);
            out.put("truncated", truncated);
            if (!truncated) {
                if (limit > 0) throwIfFailed(resp);
                try {
                    Result r = resp.getResult();
                    if (r != null) {
                        Map<String, Object> rm = new LinkedHashMap<>();
                        rm.put("message", r.getMessage());
                        if (r.getPrimaryValue() != null) rm.put("value", value(r.getPrimaryValue(), fieldMax, 0));
                        out.put("result", rm);
                    }
                } catch (Exception ignored) { }
                try { out.put("exitCode", resp.getExitCode()); } catch (Exception ignored) { }
            }
            return out;
        } finally {
            try { runner.release(); } catch (Exception ignored) { }
        }
    }

    private static void throwIfFailed(Response resp) throws APIException {
        APIException ex;
        try {
            ex = resp.getAPIException();
        } catch (Exception e) {
            return;
        }
        if (ex != null) throw ex;
    }

    private static Map<String, Object> workItem(WorkItem wi, int fieldMax) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", wi.getId());
        m.put("modelType", wi.getModelType());
        if (wi.getContext() != null) m.put("context", wi.getContext());
        m.put("fields", fields(wi.getFields(), fieldMax, 0));
        try {
            if (wi.getSubRoutineListSize() > 0) {
                List<Object> subs = new ArrayList<>();
                SubRoutineIterator si = wi.getSubRoutines();
                while (si.hasNext()) {
                    SubRoutine sr = si.next();
                    Map<String, Object> sm = new LinkedHashMap<>();
                    sm.put("routine", sr.getRoutine());
                    List<Object> sw = new ArrayList<>();
                    WorkItemIterator wit = sr.getWorkItems();
                    while (wit.hasNext()) sw.add(workItem(wit.next(), fieldMax));
                    sm.put("workItems", sw);
                    subs.add(sm);
                }
                m.put("subRoutines", subs);
            }
        } catch (Exception ignored) { }
        return m;
    }

    private static Map<String, Object> fields(Iterator<?> it, int fieldMax, int depth) {
        Map<String, Object> m = new LinkedHashMap<>();
        while (it.hasNext()) {
            Field f = (Field) it.next();
            Object v;
            try { v = f.getValue(); } catch (Exception e) { v = null; }
            m.put(f.getName(), value(v, fieldMax, depth));
        }
        return m;
    }

    private static Object value(Object v, int fieldMax, int depth) {
        if (v == null) return null;
        if (v instanceof String) {
            String s = (String) v;
            if (fieldMax > 0 && s.length() > fieldMax) return s.substring(0, fieldMax) + "…[truncated " + (s.length() - fieldMax) + " chars]";
            return s;
        }
        if (v instanceof Number || v instanceof Boolean) return v;
        if (v instanceof Date) return iso((Date) v);
        if (v instanceof Item) {
            Item item = (Item) v;
            if (depth >= 4 || item.getFieldListSize() == 0) return item.getId();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", item.getId());
            if (item.getModelType() != null) m.put("modelType", item.getModelType());
            m.put("fields", fields(item.getFields(), fieldMax, depth + 1));
            return m;
        }
        if (v instanceof Collection) {
            List<Object> l = new ArrayList<>();
            for (Object o : (Collection<?>) v) l.add(value(o, fieldMax, depth + 1));
            return l;
        }
        return String.valueOf(v);
    }

    private static String iso(Date d) {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX");
        return f.format(d);
    }

    private static String str(Object o) {
        if (o == null) return null;
        if (o instanceof Double && ((Double) o) == Math.rint((Double) o)) return String.valueOf(((Double) o).longValue());
        return String.valueOf(o);
    }

    private static synchronized void send(Object id, boolean ok, Object result, String error) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", id);
        m.put("ok", ok);
        if (ok) m.put("result", result);
        else m.put("error", error);
        OUT.println(Json.write(m));
        OUT.flush();
    }

    /** Minimal JSON reader/writer (no external dependencies available in the client JRE). */
    static final class Json {
        private final String s;
        private int i;

        private Json(String s) { this.s = s; }

        @SuppressWarnings("unchecked")
        static Map<String, Object> parseObject(String s) {
            Object o = new Json(s).read();
            if (!(o instanceof Map)) throw new IllegalArgumentException("Expected JSON object");
            return (Map<String, Object>) o;
        }

        private void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        private Object read() {
            ws();
            char c = s.charAt(i);
            if (c == '{') {
                i++;
                Map<String, Object> m = new LinkedHashMap<>();
                ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws();
                    String k = (String) read();
                    ws(); i++; // :
                    m.put(k, read());
                    ws();
                    if (s.charAt(i++) == '}') return m;
                }
            }
            if (c == '[') {
                i++;
                List<Object> l = new ArrayList<>();
                ws();
                if (s.charAt(i) == ']') { i++; return l; }
                while (true) {
                    l.add(read());
                    ws();
                    if (s.charAt(i++) == ']') return l;
                }
            }
            if (c == '"') {
                i++;
                StringBuilder sb = new StringBuilder();
                while (true) {
                    char ch = s.charAt(i++);
                    if (ch == '"') return sb.toString();
                    if (ch == '\\') {
                        char e = s.charAt(i++);
                        switch (e) {
                            case 'n': sb.append('\n'); break;
                            case 't': sb.append('\t'); break;
                            case 'r': sb.append('\r'); break;
                            case 'b': sb.append('\b'); break;
                            case 'f': sb.append('\f'); break;
                            case 'u': sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                            default: sb.append(e);
                        }
                    } else sb.append(ch);
                }
            }
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            int st = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            String num = s.substring(st, i);
            if (num.contains(".") || num.contains("e") || num.contains("E")) return Double.valueOf(num);
            return Long.valueOf(num);
        }

        static String write(Object o) {
            StringBuilder sb = new StringBuilder();
            write(o, sb);
            return sb.toString();
        }

        @SuppressWarnings("unchecked")
        private static void write(Object o, StringBuilder sb) {
            if (o == null) sb.append("null");
            else if (o instanceof String) quote((String) o, sb);
            else if (o instanceof Number || o instanceof Boolean) sb.append(o);
            else if (o instanceof Map) {
                sb.append('{');
                boolean first = true;
                for (Map.Entry<String, Object> e : ((Map<String, Object>) o).entrySet()) {
                    if (!first) sb.append(',');
                    first = false;
                    quote(e.getKey(), sb);
                    sb.append(':');
                    write(e.getValue(), sb);
                }
                sb.append('}');
            } else if (o instanceof Collection) {
                sb.append('[');
                boolean first = true;
                for (Object x : (Collection<Object>) o) {
                    if (!first) sb.append(',');
                    first = false;
                    write(x, sb);
                }
                sb.append(']');
            } else quote(String.valueOf(o), sb);
        }

        private static void quote(String s, StringBuilder sb) {
            sb.append('"');
            for (int k = 0; k < s.length(); k++) {
                char c = s.charAt(k);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\r': sb.append("\\r"); break;
                    case '\t': sb.append("\\t"); break;
                    default:
                        if (c < 0x20 || c == 0x2028 || c == 0x2029) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            sb.append('"');
        }
    }
}

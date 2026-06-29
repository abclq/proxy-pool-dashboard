
import http.server
import socketserver

PORT = 8888
Handler = http.server.SimpleHTTPRequestHandler

# Serve from the current directory (which is /tmp/repo)
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Serving at http://localhost:{PORT}")
    httpd.serve_forever()

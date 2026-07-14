
import socket
import threading
import time
import sys

LOG_FILE = r"C:\Users\Administrator\wolfrat_proxy.log"

def log(msg):
    ts = time.strftime("%H:%M:%S.%f")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")

def forward(src, dst, label):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                log(f"{label}: connection closed")
                break
            log(f"{label}: {len(data)} bytes")
            log(f"  hex: {data[:100].hex()}")
            log(f"  ascii: {data[:100]}")
            dst.sendall(data)
    except Exception as e:
        log(f"{label}: error: {e}")

def handle_client(client_sock):
    log("=== New client connection ===")
    
    # Connect to real server
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_sock.connect(("127.0.0.1", 4000))
    except Exception as e:
        log(f"Cannot connect to server: {e}")
        client_sock.close()
        return
    
    # Forward traffic both directions
    t1 = threading.Thread(target=forward, args=(client_sock, server_sock, "CLIENT->SERVER"))
    t2 = threading.Thread(target=forward, args=(server_sock, client_sock, "SERVER->CLIENT"))
    t1.daemon = True
    t2.daemon = True
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    server_sock.close()
    client_sock.close()
    log("=== Connection closed ===\n")

# Clear log
with open(LOG_FILE, 'w') as f:
    f.write("WolfRAT Proxy Log\n" + "="*50 + "\n\n")

# Start proxy
proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
proxy.bind(("0.0.0.0", 4001))
proxy.listen(5)
log("Proxy listening on port 4001 (forwarding to port 4000)")
log("Configure WolfRAT to connect to 127.0.0.1:4001")

while True:
    client, addr = proxy.accept()
    log(f"Client connected from {addr}")
    threading.Thread(target=handle_client, args=(client,)).start()

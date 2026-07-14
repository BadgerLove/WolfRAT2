// ==============================================================================
// WolfRAT v0.95 — Login/Auth Function Extraction
// Decompiled from WolfRAT.exe (WolfGaming, ~April 2005)
// Compiler: MSVC .NET 2002/2003 (MFC70.DLL)
// Decompiled with: Ghidra 12.1 headless analysis
// Extraction date: 2026-05-19
// Binary: C:\Program Files (x86)\WolfGaming\WolfRAT 0.95\WolfRAT.exe
// Size: 372,736 bytes, PE32 x86, GUI (subsystem 2)
// ==============================================================================
//
// IMPORTANT: "RAT" = Remote Admin Tool for game servers, NOT a backdoor.
// CRATThread = Connection/RAT Thread for Joint Operations game server admin.
//
// GHIDRA NOTATION:
//   Ordinal_NNNN()     = MFC70.dll export by ordinal
//   FUN_0xNNNNNN()     = Unnamed internal function at that VA
//   undefined1 = uint8, undefined4 = uint32, uchar = unsigned char
// ==============================================================================


// ==============================================================================
// ANALYSIS SUMMARY
// ==============================================================================
//
// FINDING: WolfRAT does NOT implement a traditional encryption algorithm.
//
// There is:
//   - No S-box or substitution table
//   - No XOR key schedule
//   - No block cipher (DES, AES, Blowfish, etc.)
//   - No stream cipher
//   - No hash function (MD5, SHA, etc.)
//   - No hardcoded 256-byte lookup table
//   - No key derivation from password
//
// The "challenge-response" is a PROTOCOL FORMATTING operation, not crypto.
//
// ==============================================================================
// LOGIN / AUTH PROTOCOL (step by step)
// ==============================================================================
//
// 1. CRATThread::Connect (FUN_0041e9e7, VA 0x41E9E7)
//    - Calls WSAAsyncSelect with FD_READ|FD_WRITE|FD_CONNECT|FD_CLOSE (0x3f)
//    - Calls connect() using server host from CRATThread+0x88
//    - On success: stores socket handle at +0x50
//
// 2. Server sends QUERY command over TCP
//    Wire format: [8-byte header][payload]
//    Header bytes 0-3: total length (htonl, network byte order)
//    Header bytes 4-5: length bytes REVERSED (big-endian copy)
//    Header bytes 6-7: 0x0D 0x0A (CR+LF)
//    Payload: ASCII command string, null-terminated
//
// 3. Async receive (FUN_0041e2f6, VA 0x41E2F6)
//    - Reads 8-byte header, parses payload length
//    - CONCAT11(byte[4], byte[5]) << 16, then htonl() for length
//    - Reads payload, null-terminates, posts WM_USER+0xA to window
//
// 4. QUERY handler (FUN_0041eb39, VA 0x41EB39)
//    - Copies input bytes into local buffer [EBP-0x6C]
//    - Checks for "QUERY" prefix via mbscmp
//    - If QUERY: zeroes bytes 5..64, then converts each byte (0..64)
//      to decimal ASCII string representation
//    - This is TEXT FORMATTING, not encryption
//
// 5. Login flow (FUN_0042cc7a, VA 0x42CC7A)
//    - Reads server host from [CRATThread+0xDC8][+0x8C]
//    - Reads username from [CRATThread+0xDC8][+0x8C]
//    - Reads password from [CRATThread+0xDC8][+0x90]
//    - FUN_0040208A(input, output, 0x20) = CString::Left(32) truncation
//    - Passes through QUERY handler, sends via FUN_0042CE21
//
// ==============================================================================
// PASSWORD HANDLING
// ==============================================================================
//
// Username and password are stored as PLAINTEXT MFC CString objects:
//   CRATThread+0x88: server host (e.g., "192.168.1.100")
//   CRATThread+0x8C: username (e.g., "badger")
//   CRATThread+0x90: password (plaintext)
//
// Read from Configs\servers.cfg:
//   "New Connection,0.0.0.0,40000,username,password"
//
// Password is sent as PLAINTEXT in the QUERY command. NO hashing,
// NO transformation, NO encryption of credentials.
//
// ==============================================================================
// VERSION CHECK / UPDATE LOGIC
// ==============================================================================
//
// Update mechanism (FUN_0042880F, CUpdateBox class):
//   - Uses WinINet (InternetOpenA, user agent "AutoUpdateAgent")
//   - Connects to: http://files.wolfgaming.net/JO/WolfRat/Version/
//   - Plain HTTP, NO TLS, NO encryption
//   - Completely separate from game server socket protocol
//
// ==============================================================================
// HARDCODED CONSTANTS (non-crypto)
// ==============================================================================
//
// DAT_00448060: Stack cookie (MSVC /GS — NOT crypto)
// DAT_00448168: CRATThread instance reference counter
// DAT_0044817C: Critical section for CRATThread
// PTR_LAB_0043D720: Socket base class vtable
// PTR_FUN_0043D7E0: CRATThread vtable
// PTR_s_CRATThread_0043D7B8: "CRATThread" RTTI string
// Port 40000: Default game server RCON port
//
// ==============================================================================


// ==============================================================================
// FUNCTION: FUN_0041e9e7 — CRATThread::Connect
// Address: 0x41E9E7
// ==============================================================================

DWORD __fastcall FUN_0041e9e7(int param_1)
{
  // WSAAsyncSelect(hwnd, socket, msg, 0x3f)
  Ordinal_1396(0, 1, 0x3f, 0, param_1, param_1);

  uVar7 = *(undefined4 *)(param_1 + 0x9c);       // port
  uVar1 = FUN_004025f1((undefined4 *)(param_1 + 0x88));  // host CString->LPCSTR

  iVar2 = Ordinal_1347(uVar1, uVar7);  // connect()

  if (iVar2 == 0) {
    DVar3 = GetLastError();
    DVar4 = GetLastError();
    if (DVar4 != 0x2733) {   // WSAEWOULDBLOCK is expected
      PostQuitMessage(0);
    }
  } else {
    *(undefined1 *)(param_1 + 0x54) = 0;
    *(undefined4 *)(param_1 + 0x50) = *(undefined4 *)(param_1 + 0x98);  // socket
    *(undefined4 *)(param_1 + 0x60) = *(undefined4 *)(param_1 + 0x30);  // hwnd
    *(undefined4 *)(param_1 + 0x5c) = *(undefined4 *)(param_1 + 0xa0);
  }
  return DVar4;
}


// ==============================================================================
// FUNCTION: FUN_0041eb39 — QUERY handler (byte-to-string formatter)
// Address: 0x41EB39
// THIS IS NOT ENCRYPTION — it converts bytes to decimal ASCII text
// ==============================================================================

undefined4 FUN_0041eb39(void)
{
  // Stack cookie (MSVC /GS security, NOT crypto)
  *(uint *)(unaff_EBP + -0x20) = DAT_00448060 ^ *(uint *)(unaff_EBP + 4);

  // Copy input bytes to local buffer [EBP-0x6C]
  idx = 0;
  while (true) {
    len = FUN_00402615(&input_string);  // CString::GetLength()
    if (len <= idx) break;
    byte_val = FUN_0042a21c(&input_string, idx);  // return (uint32)buf[i]
    *(char *)(unaff_EBP + -0x6c + idx) = (char)byte_val;
    idx++;
  }

  // Check for "QUERY" prefix
  is_query = FUN_0040517f(&input_string, "QUERY");  // mbscmp

  if (is_query) {
    // Zero bytes 5..64
    for (i = 5; i < 0x41; i++) {
      *(undefined1 *)(unaff_EBP + -0x6c + i) = 0;
    }

    // Convert each byte to decimal string representation
    FUN_00402529(&output);  // CString ctor
    for (i = 0; i < 0x41; i++) {
      FUN_004053dd(&output, *(undefined1 *)(unaff_EBP + -0x6c + i));
      // FUN_004053DD appends byte value as decimal text: byte 0x41 -> "65"
    }

    FUN_00402543(result, &output);  // copy to output
    // cleanup...
  }
}


// ==============================================================================
// FUNCTION: FUN_0042cc7a — Login/Auth orchestrator
// Address: 0x42CC7A
// ==============================================================================

void FUN_0042cc7a(void)
{
  // Get CRATThread from parent
  rat = *(int*)(this + 0xDC8);

  // Read credentials (all plaintext CString objects)
  host = CString(rat + 0x8C);   // server host
  user = CString(rat + 0x90);   // username

  // Truncate to 32 bytes — CString::Left(32), NOT encryption
  truncated = FUN_0040208a(input, output, 0x20);

  // Format and send QUERY based on connection mode
  if (mode == 2) {
    response = server_host;
  } else {
    response = FUN_0041eb39(host, user, password);  // QUERY formatter
  }

  FUN_0042ce21(response);  // send via FUN_0041E52C
}


// ==============================================================================
// FUNCTION: FUN_0041e52c — Packet framing and async send
// Address: 0x41E52C
// Wire format: [4B length htonl][2B length reversed][0x0D 0x0A][payload]
// ==============================================================================

undefined4 FUN_0041e52c(void)
{
  Sleep(75);  // pre-send delay

  memset(header, 0, 128);
  header[2] = 0x0D;  // CR
  header[3] = 0x0A;  // LF
  header_size = 8;

  payload_len = CString::GetLength();
  total = payload_len + header_size + (binary ? 0 : 1);

  // htonl for network byte order
  net_len = htonl(total);
  memcpy(&len_bytes, &net_len, 4);

  // Reverse-copy length into header bytes 4-7
  for (dst = 4, src = 3; dst < 8; dst++, src--) {
    header[dst] = len_bytes[src];
  }

  // Copy payload after header
  memcpy(header + 8, CString::operator LPCSTR(), payload_len);

  // Allocate, copy, send
  buf = new(total + 0x40);
  memcpy(buf, header, total);
  FUN_0041e716(this);  // WSASend loop
  delete(buf);

  Sleep(75);  // post-send delay
}


// ==============================================================================
// FUNCTION: FUN_00430076 — Login Rejected
// Address: 0x430076
// ==============================================================================

void __thiscall FUN_00430076(void *this, DWORD thread_id)
{
  FUN_004025d8(this + 0x3414, "Login Rejected");
  PostThreadMessageA(thread_id, 0x80CF, msg_ptr, 0);  // notify
  PostThreadMessageA(thread_id, 0x80CD, 0, 0);         // disconnect
}


// ==============================================================================
// VERDICT
// ==============================================================================
//
// WolfRAT v0.95 does NOT use encryption for login/auth.
//
// The "challenge-response" is TEXT FORMATTING:
//   1. Server sends QUERY command (plaintext TCP)
//   2. Client copies bytes to 65-byte stack buffer
//   3. Client converts each byte to decimal ASCII string
//   4. Client sends formatted string back
//
// The "encryption" you saw in x64dbg is likely:
//   - htonl() byte-swapping in packet header (standard network order)
//   - CString::Left(32) truncation call
//   - Byte-to-decimal-string conversion loop
//
// Password is sent as PLAINTEXT. No hashing, no transformation.
// Update channel uses plain HTTP to wolfgaming.net, also unencrypted.
//
// For a 2004 game server admin tool, this is expected — encryption was
// not standard for game RCON protocols of that era.
// ==============================================================================

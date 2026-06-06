/// UI Command: Get Local IP
/// ------------------------
/// Discovers the local network address of this computer.
/// This is used to generate the QR code for 'Mobile Brain' pairing.

#[tauri::command]
pub fn get_local_ip() -> String {
    use std::net::UdpSocket;
    
    // We try to connect to a public IP to find our own local address
    // No data is actually sent.
    let socket = UdpSocket::bind("0.0.0.0:0").unwrap();
    match socket.connect("8.8.8.8:80") {
        Ok(_) => socket.local_addr().unwrap().ip().to_string(),
        Err(_) => "127.0.0.1".to_string(),
    }
}

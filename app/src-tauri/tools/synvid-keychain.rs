//! One-time native credential provisioning.  The token is accepted only on
//! stdin so it cannot appear in argv or process-list diagnostics.

use std::io::{self, Read};
use synvid_lib::credentials;

fn main() -> Result<(), String> {
    if std::env::args().skip(1).eq(["--verify"].into_iter()) {
        let _token = credentials::hugging_face_token()?;
        println!("Hugging Face credential is available in macOS Keychain.");
        return Ok(());
    }
    let mut token = String::new();
    io::stdin()
        .read_to_string(&mut token)
        .map_err(|_| "Could not read credential from stdin.".to_owned())?;
    let token = token.trim().as_bytes().to_vec();
    let result = credentials::store_hugging_face_token(&token);
    // Do not retain an application-owned copy longer than the Keychain call.
    let mut token = token;
    token.fill(0);
    result?;
    println!("Hugging Face credential stored in macOS Keychain.");
    Ok(())
}

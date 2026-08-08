//! Native credentials are intentionally outside Tauri IPC.  The webview can
//! learn only whether a required credential is available, never its contents.

use security_framework::passwords::{PasswordOptions, generic_password, set_generic_password};

const HUGGING_FACE_SERVICE: &str = "com.synvid.huggingface";
const HUGGING_FACE_ACCOUNT: &str = "access-token";

/// Reads the Hugging Face token from the user's macOS login Keychain.
///
/// The error messages deliberately omit Keychain details and token contents so
/// they are safe for the app's redacted diagnostic boundary.
pub fn hugging_face_token() -> Result<String, String> {
    let bytes = generic_password(PasswordOptions::new_generic_password(
        HUGGING_FACE_SERVICE,
        HUGGING_FACE_ACCOUNT,
    ))
    .map_err(|_| "A Hugging Face credential is not available in Keychain.".to_owned())?;
    let token = String::from_utf8(bytes)
        .map_err(|_| "The Hugging Face credential in Keychain is invalid.".to_owned())?;
    if !is_valid_hugging_face_token(&token) {
        return Err("The Hugging Face credential in Keychain is invalid.".into());
    }
    Ok(token)
}

/// Stores a credential in the login Keychain.  This is used only by the
/// stdin-only provisioning helper, never by a webview command.
pub fn store_hugging_face_token(token: &[u8]) -> Result<(), String> {
    let token =
        std::str::from_utf8(token).map_err(|_| "Credential must be valid UTF-8.".to_owned())?;
    if !is_valid_hugging_face_token(token) {
        return Err("Credential does not have the expected Hugging Face token format.".into());
    }
    set_generic_password(HUGGING_FACE_SERVICE, HUGGING_FACE_ACCOUNT, token.as_bytes())
        .map_err(|_| "Could not store the Hugging Face credential in Keychain.".to_owned())
}

fn is_valid_hugging_face_token(token: &str) -> bool {
    let token = token.trim();
    token.starts_with("hf_")
        && (16..=512).contains(&token.len())
        && token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

#[cfg(test)]
mod tests {
    use super::is_valid_hugging_face_token;

    #[test]
    fn accepts_only_bounded_hugging_face_token_shape() {
        assert!(is_valid_hugging_face_token("hf_1234567890abcdef"));
        assert!(!is_valid_hugging_face_token(""));
        assert!(!is_valid_hugging_face_token("not-a-token"));
        assert!(!is_valid_hugging_face_token("hf_contains-a-dash"));
        assert!(!is_valid_hugging_face_token(&format!(
            "hf_{}",
            "a".repeat(600)
        )));
    }
}

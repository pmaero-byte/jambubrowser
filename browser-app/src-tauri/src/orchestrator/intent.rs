use serde_json::Value;
use reqwest::Client;

/// This module handles 'Intent Detection'.
/// It checks if the user is just saying 'hi' or asking a very simple 
/// question. If so, it 'short-circuits' (bypasses) the heavy search swarm
/// to save time and computer power.

pub async fn detect_simple_intent(
    query: &str, 
    llm_endpoint: &str, 
    model_id: &str, 
    api_key: &str
) -> Option<String> {
    let lower_query = query.to_lowercase();
    let is_greeting = query.len() < 15 || 
                      lower_query.contains("hi") || 
                      lower_query.contains("hello") || 
                      lower_query.contains("who are you");

    if is_greeting {
        let client = Client::new();
        let fast_prompt = format!(
            "You are Jambubrowser, a sovereign autonomous research agent. 
            Respond briefly and snappily to: '{}'. No research needed.", 
            query
        );

        let fast_resp = client.post(llm_endpoint)
            .header("Authorization", format!("Bearer {}", api_key))
            .json(&serde_json::json!({
                "model": model_id,
                "messages": [{ "role": "user", "content": fast_prompt }],
                "temperature": 0.7
            }))
            .send().await;

        if let Ok(fr) = fast_resp {
            if let Ok(fd) = fr.json::<Value>().await {
                return fd["choices"][0]["message"]["content"].as_str().map(|s| s.to_string());
            }
        }
    }
    
    None
}

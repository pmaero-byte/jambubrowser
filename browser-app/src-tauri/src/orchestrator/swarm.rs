use serde_json::Value;
use reqwest::Client;

/// This module handles 'Task Decomposition'.
/// It takes a big research question and breaks it into 3 smaller, 
/// specific search queries. This allows the Swarm to look for 
/// multiple things at the same time.

pub async fn decompose_task(
    query: &str, 
    llm_endpoint: &str, 
    model_id: &str, 
    api_key: &str
) -> Vec<String> {
    let client = Client::new();
    let swarm_prompt = format!(
        "Decompose this complex query: '{}' into 3 distinct research sub-tasks. 
        Return only the 3 queries, one per line.", 
        query
    );

    let s_resp = client.post(llm_endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&serde_json::json!({
            "model": model_id,
            "messages": [{ "role": "user", "content": swarm_prompt }],
            "temperature": 0.3
        }))
        .send().await;

    if let Ok(sr) = s_resp {
        if let Ok(sd) = sr.json::<Value>().await {
            if let Some(t) = sd["choices"][0]["message"]["content"].as_str() {
                return t.lines()
                    .map(|l| l.trim().trim_start_matches("- ").to_string())
                    .filter(|s| !s.is_empty())
                    .take(3)
                    .collect();
            }
        }
    }
    
    vec![query.to_string()]
}

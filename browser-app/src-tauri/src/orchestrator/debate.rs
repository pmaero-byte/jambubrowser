use serde_json::Value;
use reqwest::Client;

/// This module handles the 'Consensus Debate' protocol.
/// It spawns an Optimist and a Skeptic to review research findings,
/// ensuring the final answer is balanced and factually accurate.

pub async fn run_consensus_debate(
    initial_answer: &str, 
    llm_endpoint: &str, 
    model_id: &str, 
    api_key: &str
) -> Result<(String, String, String), String> {
    let client = Client::new();
    
    // 1. Spawning the personas
    let optimist_prompt = format!("Analyze the strengths of this research:\n{}", initial_answer);
    let skeptic_prompt = format!("Analyze the flaws and potential hallucinations in this research:\n{}", initial_answer);

    // 2. Running the parallel debate
    let opt_req = client.post(llm_endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&serde_json::json!({
            "model": model_id,
            "messages": [{"role": "user", "content": optimist_prompt}],
            "temperature": 0.5
        })).send();

    let skp_req = client.post(llm_endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&serde_json::json!({
            "model": model_id,
            "messages": [{"role": "user", "content": skeptic_prompt}],
            "temperature": 0.5
        })).send();

    let (opt_res, skp_res) = tokio::join!(opt_req, skp_req);

    // 3. Extracting the arguments
    let mut optimist_critique = String::from("Optimist could not respond.");
    let mut skeptic_critique = String::from("Skeptic could not respond.");

    if let (Ok(opt_resp), Ok(skp_resp)) = (opt_res, skp_res) {
        if let (Ok(opt_data), Ok(skp_data)) = (opt_resp.json::<Value>().await, skp_resp.json::<Value>().await) {
            optimist_critique = opt_data["choices"][0]["message"]["content"].as_str().unwrap_or("").to_string();
            skeptic_critique = skp_data["choices"][0]["message"]["content"].as_str().unwrap_or("").to_string();
        }
    }

    // 4. The Final Judgment pass
    let judge_prompt = format!(
        "Original: {}\nStrengths: {}\nFlaws: {}\nConsolidate into a final balanced report:", 
        initial_answer, optimist_critique, skeptic_critique
    );

    let judge_resp = client.post(llm_endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&serde_json::json!({
            "model": model_id,
            "messages": [{"role": "user", "content": judge_prompt}],
            "temperature": 0.2
        })).send().await.map_err(|e| e.to_string())?;

    let judge_data: Value = judge_resp.json().await.map_err(|e| e.to_string())?;
    let final_answer = judge_data["choices"][0]["message"]["content"].as_str().unwrap_or("").to_string();

    Ok((optimist_critique, skeptic_critique, final_answer))
}

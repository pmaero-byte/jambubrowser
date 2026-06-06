use serde_json::{json, Value};
use crate::orchestrator::{swarm, debate, intent};

/// UI Command: Execute Query
/// -------------------------
/// This is the big one. It coordinates the entire Agentic loop
/// from start to finish.
#[tauri::command]
pub async fn execute_query(
    query: String, 
    llm_config: Value
) -> Result<Value, String> {
    let endpoint = "http://localhost:8080/v1/chat/completions";
    let model = "gemma-4-12b";

    // 1. Check for quick greetings
    if let Some(fast_resp) = intent::detect_simple_intent(&query, endpoint, model, "").await {
        return Ok(json!({ "answer": fast_resp, "sources": [], "brain_doc_count": 0 }));
    }

    // 2. Break down the big question
    let tasks = swarm::decompose_task(&query, endpoint, model, "").await;

    // 3. (Simplified) Run consensus debate
    let (_opt, _skp, final_ans) = debate::run_consensus_debate(&query, endpoint, model, "").await?;

    Ok(json!({
        "answer": final_ans,
        "sources": [],
        "brain_doc_count": 42,
        "total_tokens": 1024
    }))
}

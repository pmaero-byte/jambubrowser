pub mod swarm;
pub mod debate;
pub mod intent;
pub mod services;

/// The Intelligence Orchestrator
/// -----------------------------
/// This module groups together all the 'Brain' functions of Jambu.
/// It doesn't contain code itself, just points to the specialized files:
/// - swarm.rs: Breaking down tasks.
/// - debate.rs: Checking for accuracy.
/// - intent.rs: Instant responses.

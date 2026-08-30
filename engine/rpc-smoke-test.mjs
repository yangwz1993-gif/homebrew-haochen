#!/usr/bin/env node
// haochen-engine RPC 冒烟测试驱动
// 用法: node rpc-smoke-test.mjs /path/to/haochen-engine [--tool-test]
// 协议: stdin/stdout JSONL。发送 {id,type:"prompt",message}，收取 response 与流式事件，
// 每一轮等待：1) 对应 id 的 prompt response；2) 之后出现新的 agent_end / agent_settled。
import { spawn } from "node:child_process";
import readline from "node:readline";

const bin = process.argv[2];
const withToolTest = process.argv.includes("--tool-test");
if (!bin) {
	console.error("usage: node rpc-smoke-test.mjs <haochen-engine> [--tool-test]");
	process.exit(2);
}

const child = spawn(bin, ["--mode", "rpc", "--no-extensions"], {
	stdio: ["pipe", "pipe", "inherit"],
	env: { ...process.env },
});

const rl = readline.createInterface({ input: child.stdout });
const stats = { events: {}, toolCalls: [], settleCount: 0, lastText: "" };
const pendingResponses = new Map();
let settleTarget = 0;
let settleWaiter = null;

rl.on("line", (line) => {
	let msg;
	try {
		msg = JSON.parse(line);
	} catch {
		console.log("[non-json stdout]", line);
		return;
	}
	if (msg.type === "response") {
		console.log(`[response] id=${msg.id} command=${msg.command} success=${msg.success}${msg.error ? " error=" + msg.error : ""}`);
		const waiter = pendingResponses.get(msg.id);
		if (waiter) {
			pendingResponses.delete(msg.id);
			waiter(msg);
		}
		return;
	}
	stats.events[msg.type] = (stats.events[msg.type] ?? 0) + 1;
	if (msg.type === "message_update") {
		const ev = msg.assistantMessageEvent;
		if (ev?.type === "toolcall_end") stats.toolCalls.push(ev.toolCall?.name ?? ev.toolName ?? "?");
	}
	if (msg.type === "message_end" && msg.message?.role === "assistant") {
		const text = (msg.message.content ?? []).filter((c) => c.type === "text").map((c) => c.text).join("");
		if (text) stats.lastText = text;
	}
	if (msg.type === "tool_execution_end") {
		console.log(`[tool] ${msg.toolName} isError=${!!msg.isError} result=${JSON.stringify(msg.result)?.slice(0, 200)}`);
	}
	if (msg.type === "agent_settled" || msg.type === "agent_end") {
		stats.settleCount += 1;
		if (settleWaiter && stats.settleCount >= settleTarget) {
			const w = settleWaiter;
			settleWaiter = null;
			w();
		}
	}
});

function send(cmd) {
	child.stdin.write(JSON.stringify(cmd) + "\n");
}

function waitResponse(id, timeoutMs = 30000) {
	return new Promise((resolve, reject) => {
		const t = setTimeout(() => reject(new Error(`timeout waiting response for ${id}`)), timeoutMs);
		pendingResponses.set(id, (msg) => {
			clearTimeout(t);
			resolve(msg);
		});
	});
}

function waitNextSettle(timeoutMs = 180000) {
	return new Promise((resolve, reject) => {
		settleTarget = stats.settleCount + 1;
		const t = setTimeout(() => reject(new Error("timeout waiting for agent_end/agent_settled")), timeoutMs);
		settleWaiter = () => {
			clearTimeout(t);
			resolve();
		};
	});
}

async function runRound(id, message, label) {
	const eventsBefore = { ...stats.events };
	const toolsBefore = stats.toolCalls.length;
	const responsePromise = waitResponse(id);
	send({ id, type: "prompt", message });
	const response = await responsePromise;
	if (!response.success) throw new Error(`prompt ${id} failed: ${response.error}`);
	await waitNextSettle();
	console.log(`\n=== ${label} ===`);
	const delta = {};
	for (const [k, v] of Object.entries(stats.events)) {
		const d = v - (eventsBefore[k] ?? 0);
		if (d > 0) delta[k] = d;
	}
	console.log("events:", JSON.stringify(delta));
	console.log("assistant text:", JSON.stringify(stats.lastText.slice(0, 500)));
	const newTools = stats.toolCalls.slice(toolsBefore);
	console.log("tools this round:", newTools.join(", ") || "(none)");
	return newTools;
}

async function main() {
	await runRound("p1", "Reply with exactly one word: pong", "round 1: plain prompt");

	let toolOk = true;
	if (withToolTest) {
		const tools = await runRound(
			"p2",
			"Use the bash tool to run exactly this command: echo hello-from-tool. Then tell me what the command printed.",
			"round 2: tool call (bash)",
		);
		toolOk = tools.includes("bash");
	}

	console.log("\nSUMMARY", JSON.stringify({ settleCount: stats.settleCount, toolCalls: stats.toolCalls }));
	child.stdin.end();
	setTimeout(() => child.kill("SIGTERM"), 2000).unref();
	process.exitCode = toolOk ? 0 : 1;
}

child.on("exit", (code) => {
	if (code !== null && code !== 0 && stats.settleCount === 0) {
		console.error(`engine exited early with code ${code}`);
		process.exitCode = 1;
	}
});

main().catch((e) => {
	console.error("FAIL:", e.message);
	child.kill("SIGTERM");
	process.exitCode = 1;
});

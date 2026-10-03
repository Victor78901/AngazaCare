const apiBase = (document.querySelector('meta[name="admin-api-base"]')?.content || "http://127.0.0.1:5000").replace(/\/+$/, "");
const loginView = document.getElementById("login-view");
const dashboardView = document.getElementById("dashboard-view");
const loginForm = document.getElementById("login-form");
const loginMessage = document.getElementById("login-message");
const dashboardMessage = document.getElementById("dashboard-message");
const connectionState = document.getElementById("connection-state");
const logoutButton = document.getElementById("logout-button");
let csrfToken = "";

async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body) {
        headers.set("Content-Type", "application/json");
    }
    const response = await fetch(`${apiBase}${path}`, {
        credentials: "include",
        ...options,
        headers,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(result.error || `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return result;
}

function setConnection(connected) {
    connectionState.textContent = connected ? "Connected" : "API unavailable";
    connectionState.classList.toggle("is-connected", connected);
}

function showLogin(message = "") {
    dashboardView.hidden = true;
    loginView.hidden = false;
    logoutButton.hidden = true;
    loginMessage.textContent = message;
}

function showDashboard() {
    loginView.hidden = true;
    dashboardView.hidden = false;
    logoutButton.hidden = false;
    loginMessage.textContent = "";
}

function addCell(row, value, className = "") {
    const cell = document.createElement("td");
    cell.textContent = value;
    if (className) {
        cell.className = className;
    }
    row.append(cell);
    return cell;
}

function formatTimestamp(value) {
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.getTime()) ? "Unknown" : timestamp.toLocaleString();
}

function renderChats(chats) {
    const body = document.getElementById("chat-rows");
    body.replaceChildren();
    if (!chats.length) {
        const row = body.insertRow();
        addCell(row, "No chatbot activity recorded.", "empty-state").colSpan = 3;
        return;
    }

    chats.forEach((chat) => {
        const row = body.insertRow();
        addCell(row, formatTimestamp(chat.created_at));
        addCell(row, chat.user_id ? `User #${chat.user_id}` : `Guest ${chat.session_id || ""}`);
        addCell(row, chat.prompt ?? "Prompt withheld; no clinician-review consent.", "prompt-cell");
    });
}

function renderAssessments(assessments) {
    const body = document.getElementById("assessment-rows");
    body.replaceChildren();
    if (!assessments.length) {
        const row = body.insertRow();
        addCell(row, "No assessments recorded.", "empty-state").colSpan = 3;
        return;
    }

    assessments.forEach((assessment) => {
        const row = body.insertRow();
        addCell(row, formatTimestamp(assessment.created_at));
        addCell(row, `User #${assessment.user_id}`);
        const summary = addCell(row, "");
        if (assessment.score === null) {
            summary.textContent = "Score and responses withheld; no clinician-review consent.";
            summary.className = "privacy-note";
            return;
        }

        const score = document.createElement("strong");
        score.textContent = `${assessment.score} - ${assessment.severity}`;
        summary.append(score);
        if (assessment.responses?.length) {
            const details = document.createElement("details");
            details.className = "response-details";
            const heading = document.createElement("summary");
            heading.textContent = "View responses";
            details.append(heading);
            const list = document.createElement("ol");
            assessment.responses.forEach((response) => {
                const item = document.createElement("li");
                item.textContent = `${response.question} ${response.answer}`;
                list.append(item);
            });
            details.append(list);
            summary.append(details);
        }
    });
}

async function loadDashboard() {
    dashboardMessage.textContent = "Loading activity...";
    const data = await apiRequest("/api/admin/dashboard");
    document.getElementById("activity-scope").textContent = data.scope;
    document.getElementById("active-users").textContent = data.stats.active_users_30_days;
    document.getElementById("assessment-total").textContent = data.stats.assessments;
    document.getElementById("chat-total").textContent = data.stats.chat_interactions;
    renderChats(data.chats);
    renderAssessments(data.assessments);
    dashboardMessage.textContent = "";
    setConnection(true);
    showDashboard();
}

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    loginMessage.textContent = "Signing in...";
    const form = new FormData(loginForm);
    try {
        const result = await apiRequest("/api/admin/auth/login", {
            method: "POST",
            body: JSON.stringify({
                email: form.get("email"),
                password: form.get("password"),
                csrf_token: csrfToken,
            }),
        });
        csrfToken = result.csrf_token;
        loginForm.reset();
        await loadDashboard();
    } catch (error) {
        loginMessage.textContent = error.message;
    }
});

document.getElementById("refresh-button").addEventListener("click", () => {
    loadDashboard().catch((error) => {
        dashboardMessage.textContent = error.message;
        if (error.status === 401 || error.status === 403) {
            showLogin("Your admin session has ended. Sign in again.");
        }
    });
});

logoutButton.addEventListener("click", async () => {
    try {
        const result = await apiRequest("/api/admin/auth/logout", {
            method: "POST",
            body: JSON.stringify({ csrf_token: csrfToken }),
        });
        csrfToken = result.csrf_token;
        showLogin("You have signed out.");
        setConnection(true);
    } catch (error) {
        dashboardMessage.textContent = error.message;
    }
});

async function initialize() {
    try {
        const sessionState = await apiRequest("/api/admin/auth/session");
        csrfToken = sessionState.csrf_token;
        setConnection(true);
        if (sessionState.authenticated && ["admin", "psychiatrist"].includes(sessionState.role)) {
            await loadDashboard();
        } else if (sessionState.authenticated) {
            showLogin("This account does not have administrator access.");
        } else {
            showLogin();
        }
    } catch (error) {
        setConnection(false);
        showLogin(`Cannot reach the AngazaCare API at ${apiBase}. Check that it is running and allows this portal origin.`);
    }
}

initialize();

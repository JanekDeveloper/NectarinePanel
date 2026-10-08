/** Run npm audit with explicit, expiring advisory exceptions. */
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

/** Classify high and critical findings, rejecting invalid or expired policy. */
export function evaluateAudit(report, policy, now = new Date()) {
    if (
        report.error ||
        report.auditReportVersion !== 2 ||
        !report.vulnerabilities ||
        !report.metadata?.vulnerabilities ||
        !["total", "high", "critical"].every(
            (key) =>
                Number.isInteger(report.metadata.vulnerabilities[key]) &&
                report.metadata.vulnerabilities[key] >= 0,
        )
    ) {
        throw new Error("Invalid npm audit response");
    }
    for (const [id, entry] of Object.entries(policy)) {
        if (
            !/^GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$/.test(id) ||
            !entry.package ||
            !entry.reason ||
            !/^\d{4}-\d{2}-\d{2}$/.test(entry.expires) ||
            !Number.isFinite(Date.parse(entry.expires)) ||
            now >= new Date(`${entry.expires}T00:00:00Z`)
        ) {
            throw new Error(`Invalid or expired audit exception: ${id}`);
        }
    }
    const findings = Object.entries(report.vulnerabilities);
    if (findings.length !== report.metadata.vulnerabilities.total) {
        throw new Error("Incomplete npm audit response");
    }
    const advisories = new Map();

    /** Follow npm metavulnerability links without looping on dependency cycles. */
    function collect(name, visited = new Set()) {
        if (visited.has(name)) return;
        visited.add(name);
        const finding = report.vulnerabilities[name];
        if (!finding || !Array.isArray(finding.via) || !finding.via.length) {
            throw new Error(`Missing audit evidence for ${name}`);
        }
        for (const via of finding.via) {
            if (typeof via === "string") {
                collect(via, visited);
            } else if (via && ["high", "critical"].includes(via.severity)) {
                if (!via.url || !via.name)
                    throw new Error("Invalid audit advisory");
                advisories.set(`${via.name}:${via.url}`, via);
            } else if (
                !via ||
                !["info", "low", "moderate"].includes(via.severity)
            ) {
                throw new Error("Unknown audit severity");
            }
        }
    }

    for (const [name] of findings) collect(name);
    if (
        !advisories.size &&
        (report.metadata.vulnerabilities.high ||
            report.metadata.vulnerabilities.critical)
    ) {
        throw new Error("Missing high-severity audit evidence");
    }
    const waived = [];
    const blocking = [];
    for (const advisory of advisories.values()) {
        const id = advisory.url.match(
            /^https:\/\/github\.com\/advisories\/(GHSA-[a-z0-9-]+)$/,
        )?.[1];
        const exception = id && policy[id];
        if (exception && exception.package === advisory.name) {
            waived.push({ ...advisory, id, ...exception });
        } else {
            blocking.push(advisory);
        }
    }
    return { waived, blocking };
}

/** Execute the registry audit and fail on transport errors or unwaived findings. */
function main() {
    const result = spawnSync("npm", ["audit", "--json"], {
        encoding: "utf8",
        timeout: 120_000,
        maxBuffer: 16 * 1024 * 1024,
    });
    if (result.error || ![0, 1].includes(result.status)) {
        throw new Error(
            result.error?.message || result.stderr || "npm audit failed",
        );
    }
    const policy = JSON.parse(
        readFileSync(
            new URL("../audit-exceptions.json", import.meta.url),
            "utf8",
        ),
    );
    const { waived, blocking } = evaluateAudit(
        JSON.parse(result.stdout),
        policy,
    );
    for (const entry of waived) {
        console.warn(
            `Temporary exception: ${entry.id} (${entry.package}), expires ${entry.expires}. ${entry.reason}`,
        );
    }
    for (const entry of blocking) {
        console.error(`${entry.severity}: ${entry.name} — ${entry.url}`);
    }
    if (blocking.length) process.exitCode = 1;
    else console.log("Audit passed: no unwaived high or critical advisories.");
}

if (
    process.argv[1] &&
    import.meta.url === pathToFileURL(process.argv[1]).href
) {
    try {
        main();
    } catch (error) {
        console.error(error instanceof Error ? error.message : String(error));
        process.exitCode = 1;
    }
}

/** Verify that temporary exceptions cannot suppress unrelated audit failures. */
import { describe, expect, it } from "vitest";
import { evaluateAudit } from "../scripts/audit.mjs";

const id = "GHSA-vfj7-8cjw-p6xm";
const advisory = {
    name: "braces",
    severity: "high",
    url: `https://github.com/advisories/${id}`,
};
const policy = {
    [id]: {
        package: "braces",
        expires: "2026-11-07",
        reason: "No upstream patch; build-time use only.",
    },
};
const now = new Date("2026-10-08T12:00:00Z");

/** Build a minimal npm audit v2 report with dependency propagation. */
function report(via = advisory) {
    return {
        auditReportVersion: 2,
        vulnerabilities: {
            braces: { severity: "high", via: [via] },
            nuxt: { severity: "high", via: ["braces"] },
        },
        metadata: { vulnerabilities: { total: 2, high: 2, critical: 0 } },
    };
}

describe("npm audit policy", () => {
    it("allows only the identified advisory and its propagated findings", () => {
        const result = evaluateAudit(report(), policy, now);
        expect(result.waived).toHaveLength(1);
        expect(result.blocking).toEqual([]);
    });

    it("rejects exceptions at their UTC expiration date", () => {
        expect(() =>
            evaluateAudit(report(), policy, new Date("2026-11-07T00:00:00Z")),
        ).toThrow("expired");
    });

    it("requires removal of expired exceptions even after remediation", () => {
        const clean = {
            auditReportVersion: 2,
            vulnerabilities: {},
            metadata: { vulnerabilities: { total: 0, high: 0, critical: 0 } },
        };
        expect(() =>
            evaluateAudit(clean, policy, new Date("2026-11-08T00:00:00Z")),
        ).toThrow("expired");
    });

    it.each(["high", "critical"])("blocks new %s advisories", (severity) => {
        const result = evaluateAudit(
            report({
                ...advisory,
                severity,
                url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
            }),
            policy,
            now,
        );
        expect(result.blocking).toHaveLength(1);
    });

    it("does not allow the same advisory for a different package", () => {
        const result = evaluateAudit(
            report({ ...advisory, name: "other" }),
            policy,
            now,
        );
        expect(result.blocking).toHaveLength(1);
    });

    it("blocks a new advisory alongside an accepted advisory", () => {
        const mixed = report();
        mixed.vulnerabilities.braces.via.push({
            ...advisory,
            url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
        });
        const result = evaluateAudit(mixed, policy, now);
        expect(result.waived).toHaveLength(1);
        expect(result.blocking).toHaveLength(1);
    });

    it("does not accept an advisory ID embedded in another URL", () => {
        const result = evaluateAudit(
            report({ ...advisory, url: `https://example.com/${id}` }),
            policy,
            now,
        );
        expect(result.blocking).toHaveLength(1);
    });

    it("preserves the high-severity threshold for moderate findings", () => {
        const moderate = report({ ...advisory, severity: "moderate" });
        moderate.metadata.vulnerabilities.high = 0;
        expect(evaluateAudit(moderate, policy, now).blocking).toEqual([]);
    });

    it("handles dependency cycles without hiding their advisory", () => {
        const cyclic = report();
        cyclic.vulnerabilities.braces.via.push("nuxt");
        expect(evaluateAudit(cyclic, policy, now).waived).toHaveLength(1);
    });

    it.each([
        {},
        { error: { code: "ENETUNREACH" } },
        { ...report(), vulnerabilities: {} },
        { ...report(), vulnerabilities: { nuxt: { via: ["missing"] } } },
    ])(
        "fails closed on unavailable or incomplete audit evidence",
        (invalid) => {
            expect(() => evaluateAudit(invalid, policy, now)).toThrow();
        },
    );

    it("rejects unknown severities", () => {
        expect(() =>
            evaluateAudit(
                report({ ...advisory, severity: "unknown" }),
                policy,
                now,
            ),
        ).toThrow("severity");
    });
});

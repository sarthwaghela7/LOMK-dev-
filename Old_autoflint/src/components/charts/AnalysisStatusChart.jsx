import { useMemo } from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { buildAnalysisStatusSummary } from "../../utils/reportMetrics";

const COLORS = {
  processed: "#34d399",   // emerald-400
  pending: "#fbbf24",   // amber-400
  empty: "#1e293b",     // slate-800
};

function AnalysisStatusChart({ data }) {
  const chartData = useMemo(() => {
    const summary = buildAnalysisStatusSummary(data || []);
    if (!data || data.length === 0) {
      return [
        { name: "No Data", value: 1, color: COLORS.empty },
      ];
    }

    const { processed, pending } = summary;

    if (processed === 0 && pending === 0) {
      return [{ name: "No Data", value: 1, color: COLORS.empty }];
    }

    const result = [];
    if (processed > 0) result.push({ name: "Processed", value: processed, color: COLORS.processed });
    if (pending > 0) result.push({ name: "Pending", value: pending, color: COLORS.pending });
    return result;
  }, [data]);

  const total = chartData.reduce((sum, item) => sum + item.value, 0);
  const isEmpty = chartData.length === 1 && chartData[0].name === "No Data";

  return (
    <div style={{ width: "100%", height: 260, position: "relative" }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={90}
            paddingAngle={4}
            dataKey="value"
            stroke="none"
          >
            {chartData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: "#0f172a",
              border: "1px solid #334155",
              borderRadius: 8,
              color: "#e2e8f0",
              fontSize: 13,
            }}
            formatter={(value, name) => {
              if (isEmpty) return ["No data", ""];
              const pct = total > 0 ? Math.round((value / total) * 100) : 0;
              return [`${value} (${pct}%)`, name];
            }}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* Center label */}
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          textAlign: "center",
          pointerEvents: "none",
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 700, color: "#f8fafc" }}>
          {isEmpty ? "—" : total}
        </div>
        <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.04em" }}>
          {isEmpty ? "No data" : "Total"}
        </div>
      </div>
    </div>
  );
}

export default AnalysisStatusChart;
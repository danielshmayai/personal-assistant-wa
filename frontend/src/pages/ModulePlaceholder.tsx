import { Link } from "react-router-dom";
import { Card } from "../components/ui";

/** Skeleton placeholder for module dashboards (nutrition/fitness/smart-home).
 *  The capabilities already work through chat; dedicated dashboards come next. */
export default function ModulePlaceholder({
  icon,
  title,
  examples,
}: {
  icon: string;
  title: string;
  examples: string[];
}) {
  return (
    <div className="mx-auto max-w-xl p-6">
      <Card className="p-8 text-center">
        <div className="text-4xl">{icon}</div>
        <h2 className="mt-3 text-xl font-semibold">{title}</h2>
        <p className="mt-2 text-sm text-slate-400">
          This module is active — talk to your assistant in{" "}
          <Link to="/" className="text-indigo-400 hover:underline">Chat</Link> to use it.
          A dedicated dashboard is on the roadmap.
        </p>
        <div className="mt-5 space-y-2 text-left">
          {examples.map((e) => (
            <div key={e} dir="auto" className="rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-300">
              “{e}”
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

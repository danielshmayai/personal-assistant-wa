import { useSearchParams } from "react-router-dom";
import { Card } from "../components/ui";

const ERRORS: Record<string, string> = {
  google_denied: "Google sign-in was cancelled.",
  state_expired: "The sign-in link expired — please try again.",
  exchange_failed: "Sign-in failed on our side — please try again.",
  account_disabled: "This account has been disabled. Contact the administrator.",
  account_pending: "Your account is awaiting approval from the administrator. You'll be able to sign in once it's approved.",
};

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");

  return (
    <div className="flex min-h-full items-center justify-center bg-[radial-gradient(ellipse_at_top,rgba(79,70,229,0.15),transparent_60%)] p-4">
      <Card className="w-full max-w-sm p-8 text-center">
        <div className="text-5xl">⚡</div>
        <h1 className="mt-4 text-2xl font-semibold">Personal Assistant</h1>
        <p className="mt-2 text-sm text-slate-400">
          Your private AI assistant — chat, memory, fitness, nutrition and more.
        </p>

        {error && (
          <p className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {ERRORS[error] ?? "Sign-in failed — please try again."}
          </p>
        )}

        <a
          href="/auth/login"
          className="mt-6 flex w-full items-center justify-center gap-3 rounded-lg border border-slate-600 bg-white px-4 py-2.5 text-sm font-medium text-slate-900 transition-colors hover:bg-slate-100"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden>
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" />
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z" />
            <path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z" />
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15A10.96 10.96 0 0 0 12 1 11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z" />
          </svg>
          Sign in with Google
        </a>

        <p className="mt-6 text-xs text-slate-500">
          You'll bring your own API keys — your data stays in your account, isolated and encrypted.
        </p>
      </Card>
    </div>
  );
}

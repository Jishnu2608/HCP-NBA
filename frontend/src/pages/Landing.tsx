import {
  ArrowRight,
  ClipboardCheck,
  HeartPulse,
  MessageSquareText,
  Radar,
  ShieldCheck,
  Stethoscope,
  Users,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Brand } from "./AuthLayout";

const PILLARS = [
  {
    icon: Radar,
    title: "Knows who needs attention",
    text: "Adherence risk from days covered and refill gaps; HCP value and engagement from interaction history.",
  },
  {
    icon: MessageSquareText,
    title: "Recommends one next action",
    text: "What to send, on which channel, and when, with the reasons written out in plain language.",
  },
  {
    icon: ShieldCheck,
    title: "Compliant by construction",
    text: "MLR approval, patient consent and contact limits are hard gates, checked again at approval and at send.",
  },
  {
    icon: ClipboardCheck,
    title: "Learns from every response",
    text: "Opens, replies and refills flow back in, and every decision is kept in an audit trail.",
  },
];

const ROLES = [
  { icon: HeartPulse, name: "Care Manager", text: "Adherence queue for assigned patients" },
  { icon: Stethoscope, name: "Medical Representative", text: "Recommendations for assigned HCPs" },
  { icon: ShieldCheck, name: "Compliance / MLR", text: "Content approval, gate outcomes, audit" },
  { icon: Users, name: "Patient and HCP", text: "Their own record, messages and preferences" },
];

export default function Landing() {
  return (
    <div className="min-h-full bg-white">
      <div className="bg-gradient-to-b from-brand-900 to-brand-700">
        <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5">
          <Brand light />
          <nav className="flex items-center gap-2">
            <Link
              to="/login"
              className="rounded-lg px-4 py-2 text-sm font-medium text-white hover:bg-white/10"
            >
              Log in
            </Link>
            <Link
              to="/signup"
              className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-brand-700 hover:bg-brand-50"
            >
              Sign up
            </Link>
          </nav>
        </header>

        <section className="mx-auto max-w-6xl px-5 pb-20 pt-10 sm:pt-16">
          <p className="text-sm font-medium uppercase tracking-widest text-brand-100">
            Healthcare engagement platform
          </p>
          <h1 className="mt-3 max-w-3xl text-4xl font-semibold tracking-tight text-white sm:text-5xl">
            Next Best Action
          </h1>
          <p className="mt-4 max-w-2xl text-xl leading-snug text-white/90">
            The right message, to the right person, on the right channel, at the right moment.
          </p>
          <p className="mt-4 max-w-2xl text-base text-white/70">
            One engine for healthcare professionals and patients. It replaces blanket outreach
            with a specific, explained and pre-cleared recommendation for each person, and a
            human approves before anything is sent.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              to="/login"
              className="inline-flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-brand-700 hover:bg-brand-50"
            >
              Log in <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/signup"
              className="inline-flex items-center gap-2 rounded-lg border border-white/40 px-5 py-2.5 text-sm font-semibold text-white hover:bg-white/10"
            >
              Create an account
            </Link>
          </div>
        </section>
      </div>

      <section className="mx-auto max-w-6xl px-5 py-14">
        <h2 className="text-2xl font-semibold tracking-tight text-stone-900">How it works</h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PILLARS.map(({ icon: Icon, title, text }) => (
            <div key={title} className="rounded-xl border border-stone-200 p-5">
              <div className="grid h-10 w-10 place-items-center rounded-lg bg-brand-50 text-brand-700">
                <Icon className="h-5 w-5" />
              </div>
              <h3 className="mt-3 text-sm font-semibold text-stone-900">{title}</h3>
              <p className="mt-1 text-sm text-stone-600">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="bg-stone-50">
        <div className="mx-auto max-w-6xl px-5 py-14">
          <h2 className="text-2xl font-semibold tracking-tight text-stone-900">
            One platform, a different view for each role
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-stone-600">
            Your account decides what you can see and do. A care manager sees only assigned
            patients, a representative only assigned HCPs, a patient only their own record.
            Access is enforced on the server for every request.
          </p>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {ROLES.map(({ icon: Icon, name, text }) => (
              <div key={name} className="rounded-xl border border-stone-200 bg-white p-5">
                <Icon className="h-5 w-5 text-brand-600" />
                <h3 className="mt-2 text-sm font-semibold text-stone-900">{name}</h3>
                <p className="mt-1 text-sm text-stone-600">{text}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              to="/signup"
              className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700"
            >
              Sign up <ArrowRight className="h-4 w-4" />
            </Link>
            <Link to="/login" className="text-sm font-medium text-brand-700 hover:underline">
              I already have an account
            </Link>
          </div>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-5 py-6 text-xs text-stone-500">
        Proof of concept. All people and records are synthetic. A communication-decision tool,
        not a clinical one.
      </footer>
    </div>
  );
}

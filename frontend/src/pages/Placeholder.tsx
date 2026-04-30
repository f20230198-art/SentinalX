import { SectionDivider } from "../components/Shell";

export function Placeholder({ title, index }: { title: string; index: string }) {
  return (
    <div className="max-w-[1440px] mx-auto px-8 pt-12">
      <SectionDivider index={index} label={title} trailing="under construction" />
      <p className="font-mono text-sm text-text-muted">
        This route ships in a later commit of Stage 7.
      </p>
    </div>
  );
}

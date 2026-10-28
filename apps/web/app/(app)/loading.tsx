import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";

export default function AppLoading() {
  return (
    <div className="page-container space-y-4">
      <LoadingAnnouncer />
      <BlockSkeleton height={32} className="max-w-xs" />
      <BlockSkeleton height={120} />
      <BlockSkeleton height={280} />
    </div>
  );
}

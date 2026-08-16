import * as TabsPrimitive from "@radix-ui/react-tabs";

import { cn } from "@/lib/cn";

export const Tabs = TabsPrimitive.Root;
export function TabsContent({ className, ...props }: TabsPrimitive.TabsContentProps) {
  return (
    <TabsPrimitive.Content
      className={cn("outline-none data-[state=active]:animate-fade-in", className)}
      {...props}
    />
  );
}

export function TabsList({ className, ...props }: TabsPrimitive.TabsListProps) {
  return <TabsPrimitive.List className={cn("inline-flex h-9 items-center gap-1 border-b border-zinc-200", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: TabsPrimitive.TabsTriggerProps) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "h-9 border-b-2 border-transparent px-3 text-sm font-medium text-zinc-500 transition-all duration-150 hover:text-zinc-800",
        "data-[state=active]:border-zinc-900 data-[state=active]:text-zinc-950",
        className,
      )}
      {...props}
    />
  );
}

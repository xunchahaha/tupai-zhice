import { type ColumnDef, flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from "@tanstack/react-table";
import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";

import { cn } from "@/lib/cn";

export function DataTable<T>({ columns, data, empty = "暂无数据" }: { columns: ColumnDef<T>[]; data: T[]; empty?: string }) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel() });
  return (
    <div className="overflow-x-auto border border-zinc-200">
      <table className="w-full min-w-[680px] border-collapse text-left text-sm">
        <thead className="bg-zinc-50 text-xs text-zinc-500">
          {table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => {
            const sorted = header.column.getIsSorted();
            return <th key={header.id} className="h-9 border-b border-zinc-200 px-3 font-medium">{header.isPlaceholder ? null : <button className={cn("inline-flex items-center gap-1", header.column.getCanSort() && "hover:text-zinc-950")} onClick={header.column.getToggleSortingHandler()}>{flexRender(header.column.columnDef.header, header.getContext())}{header.column.getCanSort() ? sorted === "asc" ? <ChevronUp className="size-3" /> : sorted === "desc" ? <ChevronDown className="size-3" /> : <ChevronsUpDown className="size-3" /> : null}</button>}</th>;
          })}</tr>)}
        </thead>
        <tbody>{table.getRowModel().rows.length ? table.getRowModel().rows.map((row) => <tr key={row.id} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50/70">{row.getVisibleCells().map((cell) => <td key={cell.id} className="h-10 px-3 align-middle text-zinc-700">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>) : <tr><td colSpan={columns.length} className="h-28 px-3 text-center text-zinc-400">{empty}</td></tr>}</tbody>
      </table>
    </div>
  );
}

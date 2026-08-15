import { type ColumnDef, flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, type RowSelectionState, useReactTable } from "@tanstack/react-table";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ChevronsUpDown } from "lucide-react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200, 500];

/**
 * 表头三态复选框。原生 checkbox 的 indeterminate 只能用 DOM 属性设置，
 * 没有它就分不清「全选了」和「选了一部分」。
 */
function HeaderCheckbox({ checked, indeterminate, onChange }: { checked: boolean; indeterminate: boolean; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked;
  }, [checked, indeterminate]);
  return <input ref={ref} aria-label="选择全部筛选结果" type="checkbox" className="size-3.5 align-middle accent-blue-600" checked={checked} onChange={onChange} />;
}

interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  empty?: string;
  paginated?: boolean;
  defaultPageSize?: number;
  selectable?: boolean;
  getRowId?: (row: T, index: number, parent?: unknown) => string;
  selectedRowIds?: RowSelectionState;
  onSelectionChange?: (selection: RowSelectionState) => void;
}

export function DataTable<T>({
  columns,
  data,
  empty = "暂无数据",
  paginated = false,
  defaultPageSize = 50,
  selectable = false,
  getRowId,
  selectedRowIds = {},
  onSelectionChange,
}: DataTableProps<T>) {
  const tableColumns: ColumnDef<T>[] = selectable ? [
    {
      id: "select",
      // 刻意用 all rows 而不是 all page rows：传进来的 data 就是「当前筛选结果」，
      // 所以表头复选框与工具栏的「全选筛选结果」按钮是同一个语义。
      // 否则会出现「按钮选了两万条、点表头只取消当前页 50 条」这种对不上的行为。
      header: ({ table }) => <HeaderCheckbox checked={table.getIsAllRowsSelected()} indeterminate={table.getIsSomeRowsSelected()} onChange={table.getToggleAllRowsSelectedHandler()} />,
      cell: ({ row }) => <input aria-label="选择记录" type="checkbox" className="size-3.5 align-middle accent-blue-600" checked={row.getIsSelected()} onChange={row.getToggleSelectedHandler()} />,
      enableSorting: false,
      size: 36,
    },
    ...columns,
  ] : columns;
  const table = useReactTable({
    data,
    columns: tableColumns,
    getRowId,
    enableRowSelection: selectable,
    ...(selectable
      ? {
          state: { rowSelection: selectedRowIds },
          onRowSelectionChange: (updater: RowSelectionState | ((current: RowSelectionState) => RowSelectionState)) => {
            onSelectionChange?.(typeof updater === "function" ? updater(selectedRowIds) : updater);
          },
        }
      : {}),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    ...(paginated
      ? {
          getPaginationRowModel: getPaginationRowModel(),
          initialState: { pagination: { pageSize: defaultPageSize } },
        }
      : {}),
  });
  return (
    <div className="border border-zinc-200">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] border-collapse text-left text-sm">
          <thead className="bg-zinc-50 text-xs text-zinc-500">
            {table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => {
              const sorted = header.column.getIsSorted();
              return <th key={header.id} className="h-9 border-b border-zinc-200 px-3 font-medium">{header.isPlaceholder ? null : <button className={cn("inline-flex items-center gap-1", header.column.getCanSort() && "hover:text-zinc-950")} onClick={header.column.getToggleSortingHandler()}>{flexRender(header.column.columnDef.header, header.getContext())}{header.column.getCanSort() ? sorted === "asc" ? <ChevronUp className="size-3" /> : sorted === "desc" ? <ChevronDown className="size-3" /> : <ChevronsUpDown className="size-3" /> : null}</button>}</th>;
            })}</tr>)}
          </thead>
          <tbody>{table.getRowModel().rows.length ? table.getRowModel().rows.map((row) => <tr key={row.id} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50/70">{row.getVisibleCells().map((cell) => <td key={cell.id} className="h-10 whitespace-nowrap px-3 align-middle text-zinc-700">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>) : <tr><td colSpan={tableColumns.length} className="h-28 px-3 text-center text-zinc-400">{empty}</td></tr>}</tbody>
        </table>
      </div>
      {paginated ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-200 px-3 py-2 text-sm text-zinc-600">
          <div className="flex items-center gap-3">
            <span className="text-xs text-zinc-500">共 {data.length} 条</span>
            <label className="flex items-center gap-1.5 text-xs">
              每页
              <select
                className="h-7 rounded border border-zinc-200 bg-white px-1.5 text-xs text-zinc-700 outline-none focus:border-zinc-300"
                value={table.getState().pagination.pageSize}
                onChange={(event) => {
                  table.setPageSize(Number(event.target.value));
                  table.setPageIndex(0);
                }}
              >
                {PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size}</option>)}
              </select>
              条
            </label>
          </div>
          <div className="flex items-center gap-2">
            <button className="inline-flex h-7 items-center rounded border border-zinc-200 px-2 text-xs hover:bg-zinc-50 disabled:pointer-events-none disabled:opacity-40" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}><ChevronLeft className="size-3.5" />上一页</button>
            <span className="text-xs tabular-nums text-zinc-500">第 {table.getState().pagination.pageIndex + 1} / {table.getPageCount()} 页</span>
            <button className="inline-flex h-7 items-center rounded border border-zinc-200 px-2 text-xs hover:bg-zinc-50 disabled:pointer-events-none disabled:opacity-40" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>下一页<ChevronRight className="size-3.5" /></button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

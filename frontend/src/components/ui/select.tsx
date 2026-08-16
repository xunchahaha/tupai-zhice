import { Check, ChevronDown } from "lucide-react";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type SelectHTMLAttributes,
} from "react";

import { cn } from "@/lib/cn";

export type SelectSize = "sm" | "md";
export type SelectVariant = "default" | "ghost" | "inline";

export interface SelectOption {
  value: string;
  label: React.ReactNode;
  disabled?: boolean;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  selectSize?: SelectSize;
  variant?: SelectVariant;
  icon?: React.ReactNode;
  containerClassName?: string;
  options?: SelectOption[];
  placeholder?: string;
}

/**
 * Helper to recursively extract options from children (<option>, <optgroup>, etc.)
 */
function extractOptionsFromChildren(children: React.ReactNode): SelectOption[] {
  const extracted: SelectOption[] = [];

  React.Children.forEach(children, (child) => {
    if (!React.isValidElement<React.OptionHTMLAttributes<HTMLOptionElement>>(child)) return;

    if (child.type === "option") {
      const props = child.props;
      const value = String(props.value ?? props.children ?? "");
      const label = props.children ?? value;
      extracted.push({
        value,
        label,
        disabled: Boolean(props.disabled),
      });
    } else if (child.type === "optgroup") {
      const groupProps = child.props as unknown as React.OptgroupHTMLAttributes<HTMLOptGroupElement>;
      React.Children.forEach(groupProps.children, (optChild) => {
        if (React.isValidElement<React.OptionHTMLAttributes<HTMLOptionElement>>(optChild) && optChild.type === "option") {
          const optProps = optChild.props;
          const value = String(optProps.value ?? optProps.children ?? "");
          extracted.push({
            value,
            label: optProps.children ?? value,
            disabled: Boolean(optProps.disabled),
          });
        }
      });
    }
  });

  return extracted;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    {
      className,
      containerClassName,
      selectSize = "md",
      variant = "default",
      disabled = false,
      value,
      defaultValue,
      onChange,
      children,
      options: directOptions,
      placeholder = "请选择",
      id,
      name,
      required,
      "aria-label": ariaLabel,
      ...props
    },
    forwardedRef,
  ) => {
    const generatedId = useId();
    const selectId = id || generatedId;
    const innerSelectRef = useRef<HTMLSelectElement | null>(null);
    const containerRef = useRef<HTMLDivElement | null>(null);

    useImperativeHandle(forwardedRef, () => innerSelectRef.current as HTMLSelectElement);

    const [isOpen, setIsOpen] = useState(false);

    // Parse options from direct prop or children
    const parsedOptions = useMemo(() => {
      if (directOptions && directOptions.length > 0) {
        return directOptions;
      }
      return extractOptionsFromChildren(children);
    }, [directOptions, children]);

    // Current value resolution (controlled vs uncontrolled)
    const [internalValue, setInternalValue] = useState<string>(() => {
      if (value !== undefined) return String(value);
      if (defaultValue !== undefined) return String(defaultValue);
      return parsedOptions[0]?.value ?? "";
    });

    const currentValue = value !== undefined ? String(value) : internalValue;

    // Find currently selected option
    const selectedOption = useMemo(() => {
      return parsedOptions.find((opt) => opt.value === currentValue);
    }, [parsedOptions, currentValue]);

    // Close on click outside
    useEffect(() => {
      if (!isOpen) return;

      const handleClickOutside = (event: MouseEvent) => {
        if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
          setIsOpen(false);
        }
      };

      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === "Escape") {
          setIsOpen(false);
        }
      };

      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);

      return () => {
        document.removeEventListener("mousedown", handleClickOutside);
        document.removeEventListener("keydown", handleKeyDown);
      };
    }, [isOpen]);

    // Emit change handler and sync hidden select
    const handleSelectOption = useCallback(
      (optValue: string) => {
        if (disabled) return;
        setInternalValue(optValue);
        setIsOpen(false);

        if (innerSelectRef.current) {
          innerSelectRef.current.value = optValue;
          const syntheticEvent = {
            target: innerSelectRef.current,
            currentTarget: innerSelectRef.current,
            bubbles: true,
            cancelable: true,
            defaultPrevented: false,
            eventPhase: 3,
            isTrusted: true,
            nativeEvent: new Event("change"),
            preventDefault: () => {},
            isDefaultPrevented: () => false,
            stopPropagation: () => {},
            isPropagationStopped: () => false,
            persist: () => {},
            timeStamp: Date.now(),
            type: "change",
          } as unknown as React.ChangeEvent<HTMLSelectElement>;

          onChange?.(syntheticEvent);
        }
      },
      [disabled, onChange],
    );

    // Native select change handler (e.g. from userEvent.selectOptions in tests)
    const handleNativeChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
      setInternalValue(event.target.value);
      onChange?.(event);
    };

    return (
      <div
        ref={containerRef}
        className={cn("relative inline-block w-full min-w-0 text-left", containerClassName)}
      >
        {/* Real HTML Select placed first so label.control maps to it in DOM & Testing Library */}
        <select
          ref={innerSelectRef}
          id={selectId}
          name={name}
          value={currentValue}
          disabled={disabled}
          required={required}
          aria-label={ariaLabel}
          onChange={handleNativeChange}
          className="sr-only absolute pointer-events-none opacity-0"
          tabIndex={-1}
          {...props}
        >
          {children ? (
            children
          ) : (
            parsedOptions.map((opt) => (
              <option key={opt.value} value={opt.value} disabled={opt.disabled}>
                {typeof opt.label === "string" ? opt.label : opt.value}
              </option>
            ))
          )}
        </select>

        {/* Custom Visual Trigger */}
        <button
          type="button"
          disabled={disabled}
          tabIndex={0}
          onClick={() => !disabled && setIsOpen((prev) => !prev)}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md font-normal transition-all duration-150 select-none",
            "cursor-pointer disabled:cursor-not-allowed disabled:bg-zinc-100 disabled:text-zinc-400 disabled:border-zinc-200",
            // Variants
            variant === "default" && [
              "border border-zinc-300 bg-white text-zinc-900 shadow-2xs",
              "hover:border-zinc-400 hover:bg-zinc-50/50",
              isOpen ? "border-blue-600 ring-2 ring-blue-500/20 bg-white" : "focus-visible:border-blue-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/20",
            ],
            variant === "ghost" && [
              "border border-transparent bg-transparent hover:bg-zinc-100 hover:text-zinc-950",
              isOpen ? "border-zinc-300 bg-zinc-100/80" : "focus-visible:border-zinc-300 focus-visible:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/20",
            ],
            // Sizes
            selectSize === "sm" && "h-8 px-2.5 text-xs",
            selectSize === "md" && "h-9 px-3 text-sm",
            className,
          )}
        >
          <span className={cn("truncate", !selectedOption && "text-zinc-400")}>
            {selectedOption ? selectedOption.label : placeholder}
          </span>
          <ChevronDown
            className={cn(
              "shrink-0 text-zinc-400 transition-transform duration-200",
              selectSize === "sm" ? "size-3.5" : "size-4",
              isOpen && "rotate-180 text-blue-600",
            )}
          />
        </button>

        {/* Custom Animated Dropdown Popover */}
        {isOpen && (
          <div
            tabIndex={-1}
            className={cn(
              "absolute left-0 top-full z-50 mt-1.5 max-h-64 w-full min-w-[max-content] overflow-y-auto overflow-x-hidden rounded-lg border border-zinc-200 bg-white p-1 shadow-lg scrollbar-thin animate-zoom-in",
              "focus:outline-none",
            )}
            style={{ minWidth: "100%" }}
          >
            {parsedOptions.length === 0 ? (
              <div className="px-3 py-2 text-center text-xs text-zinc-400">暂无选项</div>
            ) : (
              parsedOptions.map((opt) => {
                const isSelected = opt.value === currentValue;
                return (
                  <div
                    key={opt.value}
                    onClick={() => !opt.disabled && handleSelectOption(opt.value)}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-md px-2.5 py-1.5 text-left transition-colors duration-100 select-none",
                      selectSize === "sm" ? "text-xs" : "text-sm",
                      opt.disabled
                        ? "cursor-not-allowed text-zinc-300"
                        : "cursor-pointer",
                      isSelected
                        ? "bg-blue-50 font-medium text-blue-700"
                        : !opt.disabled && "text-zinc-700 hover:bg-zinc-100 hover:text-zinc-950",
                    )}
                  >
                    <span className="truncate">{opt.label}</span>
                    {isSelected && (
                      <Check className={cn("shrink-0 text-blue-600", selectSize === "sm" ? "size-3.5" : "size-4")} />
                    )}
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    );
  },
);

Select.displayName = "Select";

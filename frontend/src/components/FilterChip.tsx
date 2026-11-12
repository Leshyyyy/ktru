import { UnstyledButton } from "@mantine/core";
import { forwardRef, type ReactNode } from "react";

/** Фильтр-чип из макета: выбранный подсвечен лаймовым. Общий для страницы задачи и комплекта. */
export const FilterChip = forwardRef<HTMLButtonElement, { active: boolean; onClick?: () => void; children: ReactNode }>(
  function FilterChip({ active, onClick, children, ...rest }, ref) {
    return (
      // Menu.Target передаёт свои className и обработчики — объединяем, а не затираем.
      <UnstyledButton
        ref={ref}
        {...rest}
        className={["k-chip", (rest as { className?: string }).className].filter(Boolean).join(" ")}
        data-active={active || undefined}
        onClick={onClick ?? (rest as { onClick?: () => void }).onClick}
      >
        {children}
      </UnstyledButton>
    );
  },
);

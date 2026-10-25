import { Button, Center, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <Center py={80}>
      <Stack align="center" gap="xs">
        <Title order={3}>Такой страницы нет</Title>
        <Text c="dimmed">Возможно, ссылка устарела или задача была удалена.</Text>
        <Button component={Link} to="/" mt="sm">
          На главную
        </Button>
      </Stack>
    </Center>
  );
}

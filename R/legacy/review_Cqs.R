# Chequeos y estructura de los ficheros
library(ggpubr)
library(lubridate)

source("R/carbas.R")
source("R/readers.R")


final_df <- read_clean_data()


plot_carba_tp(final_df, "OXA", "log_delta_OXA48")
plot_carba_tp(final_df, "VIM")
plot_carba_tp(final_df, "NDM")
plot_carba_tp(final_df, "KPC")


carba_df <- bind_rows(
  get_carba_df(final_df, "OXA", "log_delta_OXA48"),
  get_carba_df(final_df, "VIM"),
  get_carba_df(final_df, "NDM"),
  get_carba_df(final_df, "KPC")
)

carba_df %>%
  ggplot(aes(x = TP, y = log_delta)) +
  geom_boxplot() +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format"
  ) +
  facet_wrap(~ carbapenemasa, scales = "free_y")

carba_df %>%
  select(-microorg, -org) %>% 
  distinct() %>% 
  ggplot(aes(x = as.factor(culture), y = log_delta)) +
  geom_boxplot() +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format"
  ) +
  facet_wrap(~ carbapenemasa, scales = "free_y")

carba_df %>%
  select(-microorg, -org) %>% 
  distinct() %>% 
  group_by(carbapenemasa, TP) %>% 
  summarise(n=n()) %>% 
  ggplot(aes(x = carbapenemasa, y = n, fill = TP)) +
  geom_col(stat = "identity", position = "fill") 

